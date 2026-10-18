"""PerkPilot FastAPI application.

Modes (PERKPILOT_MODE):
* demo    : the fictional fixture store; payments, refunds and shipping are simulated (tests and the evaluation runner).
* live    : advisory only; no seed, no simulated effects.
* shopify : Shopify is the commerce platform. Checkout is a Shopify draft order, the purchase record is built from the
            Shopify order, and refunds, exchanges and fulfillment run through the Admin API.
"""

from __future__ import annotations

import json
import os
import threading

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import auth, demo, learning, metrics, resolution, shopping, shopify_api, shopify_stream
from .ai import ai_status
from .core import MERCHANT_ID, POLICY_ID, DomainError, live_mode, load_env, mode, now, shopify_mode
from .evaluation import run_evaluation
from .fixtures import seed
from .models import (AdvanceRequest, CheckoutConfirmRequest, ClaimRequest, ClarifyRequest, ConfirmIntentRequest, HistoryRequest,
                     OutcomeRequest, ParseRequest, RecommendRequest, RemedyAcceptRequest, RuleDecisionRequest, RuleToggleRequest,
                     ScenarioRequest, SessionRequest, SettingsPatch, VerifyRequest)
from .pricing import catalog_facets, sizes_in_order
from .records import customer_purchase, integrity, is_verified, next_delivery_time, shipment_for
from .store import BaseStore, create_store
from .verifier import active_rules_for

MERCHANT_PREFIXES = ("/api/merchant", "/api/eval")


def require_admin(authorization: str = Header(default="")) -> str:
    """Audit data (record hashes, event chain, per-rule checks, internal IDs) needs merchant authentication in every
    mode: a Shopify admin session token from the embedded app, or the PERKPILOT_ADMIN_TOKEN bearer."""
    if not os.environ.get("PERKPILOT_ADMIN_TOKEN") and not os.environ.get("SHOPIFY_CLIENT_SECRET"):
        raise DomainError("Audit access is disabled. Set PERKPILOT_ADMIN_TOKEN on the server to enable it.", 401, "admin_not_configured")
    actor = auth.merchant_actor(authorization)
    if not actor:
        raise DomainError("Admin authentication required.", 401, "unauthorized")
    return actor


def customer_quote(q: dict) -> dict:
    return {k: v for k, v in q.items() if k != "quote_hash"}


class PriceChange(BaseModel):
    price_cents: int = Field(ge=100, le=1_000_00)


def create_app(store: BaseStore | None = None) -> FastAPI:
    load_env()
    m = mode()
    demo_mode = m == "demo"
    app = FastAPI(title="PerkPilot API", version="2.0.0", description={
        "demo": "Verified AI commerce demo. All payments, refunds and shipments are simulated.",
        "live": "Verified AI commerce assistant (live mode: no simulated payments, refunds or shipping).",
        "shopify": "Verified AI commerce on Shopify: checkout, payments, refunds and fulfillment run in the merchant's Shopify store.",
    }[m])
    # Shopper and merchant calls authenticate with bearer tokens, never cookies, so a permissive origin policy is safe
    # for the storefront embed (the store's own domain calls /api/session and /api/storefront).
    origins = ["*"] if m != "live" else [o.strip() for o in os.environ.get("PERKPILOT_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])
    app.state.store = store or create_store(live=live_mode(), filename="shopify_state.json" if m == "shopify" else "demo_state.json")
    if demo_mode and not app.state.store.get("meta", "seed_info"):
        seed(app.state.store)
    if m == "shopify":
        from . import shopify_commerce

        shopify_commerce.ensure_defaults(app.state.store, os.environ.get("PERKPILOT_STORE_NAME"))
        if shopify_api.configured() and not app.state.store.find("products", {"source": "shopify"}) and store is None:
            threading.Thread(target=_initial_sync, args=(app.state.store,), daemon=True).start()

    if m == "live" and not os.environ.get("PERKPILOT_ADMIN_TOKEN", ""):
        raise RuntimeError("PERKPILOT_MODE=live requires PERKPILOT_ADMIN_TOKEN.")
    if m == "shopify" and not (os.environ.get("PERKPILOT_ADMIN_TOKEN") or os.environ.get("SHOPIFY_CLIENT_SECRET")):
        raise RuntimeError("PERKPILOT_MODE=shopify requires SHOPIFY_CLIENT_SECRET (or PERKPILOT_ADMIN_TOKEN).")

    if not demo_mode:
        @app.middleware("http")
        async def _merchant_auth(request: Request, call_next):
            if request.url.path.startswith(MERCHANT_PREFIXES) and request.method != "OPTIONS":
                actor = auth.merchant_actor(request.headers.get("authorization", ""))
                if not actor:
                    return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "Merchant authentication required."}})
                request.state.merchant_actor = actor
            return await call_next(request)

    def S() -> BaseStore:
        return app.state.store

    def shopper_visible(p: dict) -> bool:
        # Outside the demo, a simulated payment or shipment is never presented as a real order.
        return demo_mode or not p["payment_method_reference"].get("simulated", True)

    def shopper_of(request: Request, claimed: str | None = None) -> str:
        """The caller's shopper id: from a signed session token, or (demo/live) the persona the caller names."""
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        session = auth.read_session(token) if token else None
        if session:
            if claimed and claimed != session["sid"]:
                raise DomainError("This session belongs to a different shopper.", 403, "forbidden")
            auth.ensure_shopper(S(), session["sid"], session.get("cid"))
            return session["sid"]
        if m == "shopify":
            raise DomainError("Start a shopping session first.", 401, "session_required")
        if not claimed:
            raise DomainError("shopper_id is required.", 422, "invalid_request")
        return claimed

    def owned(request: Request, owner: str, claimed: str | None = None) -> str:
        sid = shopper_of(request, claimed) if (m == "shopify" or claimed or request.headers.get("authorization")) else owner
        if sid != owner:
            raise DomainError("Not found for this shopper.", 404, "not_found")
        return sid

    def merchant(request: Request, fallback: str) -> str:
        return getattr(request.state, "merchant_actor", None) or fallback

    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message, "detail": exc.detail}})

    # ------------------------------------------------------------ meta

    @app.get("/api/health")
    def health():
        return {"ok": True, "mode": m, "store": S().mode, "ai": ai_status(), "now": now(S()).isoformat(),
                "shopify": {"configured": shopify_api.configured(), "shop": shopify_api.shop_domain()}}

    @app.get("/api/app/config")
    def app_config():
        """Public: what the embedded admin and the storefront embed need to boot (the client id is App Bridge's public API key)."""
        merchant = S().get("merchants", MERCHANT_ID) or {}
        return {"mode": m, "client_id": os.environ.get("SHOPIFY_CLIENT_ID", ""), "shop": shopify_api.shop_domain(),
                "store_name": merchant.get("name"), "ai": ai_status(), "now": now(S()).isoformat(),
                "payment_methods": S().find("payment_methods") if not demo_mode else None}

    if demo_mode:
        register_demo_routes(app, S)

    # ------------------------------------------------------------ shopper sessions

    @app.post("/api/session/anonymous")
    def session_anonymous(body: SessionRequest):
        return auth.issue_session(S(), previous=body.previous_token)

    @app.get("/api/proxy/session")
    def proxy_session(request: Request):
        """Reached through the Shopify app proxy (/apps/perkpilot/session). Shopify signs the query and, for a signed-in
        customer, includes logged_in_customer_id; that's the only way PerkPilot trusts a customer identity."""
        q = list(request.query_params.multi_items())
        if not auth.verify_app_proxy(q, os.environ.get("SHOPIFY_CLIENT_SECRET", "")):
            raise DomainError("App proxy signature is invalid.", 401, "invalid_signature")
        params = dict(q)
        if params.get("shop") and params["shop"] != shopify_api.shop_domain():
            raise DomainError("Request is for a different shop.", 403, "wrong_shop")
        return auth.issue_session(S(), customer_id=params.get("logged_in_customer_id") or None,
                                  previous=request.headers.get("x-perkpilot-session"))

    # ------------------------------------------------------------ catalog

    @app.get("/api/products")
    def products(category: str | None = None):
        items = S().find("products", {"merchant_id": MERCHANT_ID, "status": "active"})
        if category:
            items = [p for p in items if p["category"] == category]
        return {"products": sorted(items, key=lambda p: (p["category"] != "running_shoes", p["name"]))}

    @app.get("/api/catalog/facets")
    def facets():
        return catalog_facets(S())

    @app.get("/api/catalog/examples")
    def examples():
        """Example requests built from the products this store actually sells."""
        out = []
        for p in sorted(S().find("products", {"status": "active"}), key=lambda p: p["name"])[:6]:
            v = next((v for v in p["variants"] if p["inventory_by_variant"].get(v["variant_id"], 0) > 0), None)
            if not v:
                continue
            words = " ".join(w for w in [v["color"], p["category"].replace("_", " "), f"in size {v['size']}" if v["size"] else ""] if w)
            budget = ((v.get("price_cents", p["price_cents"]) + 25_00) // 5_00) * 5
            out.append(f"A {words} under ${budget} including shipping and tax, delivered by Friday")
        return {"examples": out[:3]}

    @app.get("/api/products/{product_id}")
    def product(product_id: str):
        p = S().get("products", product_id)
        if not p:
            raise DomainError("Unknown product.", 404, "not_found")
        sizes = sizes_in_order(p)
        guidance = {size: [{"rule_id": r["rule_id"], "message": r["customer_message"], "suggest_size": r["proposed_action"]["suggest_size"]}
                           for r in active_rules_for(S(), product_id, size)] for size in sizes}
        offers = [o for o in S().find("offers") if o["status"] == "active" and (not o["eligible_products"] or product_id in o["eligible_products"])]
        return {"product": p, "guidance_by_size": {k: v for k, v in guidance.items() if v}, "offers": offers}

    @app.get("/api/storefront/guidance")
    def storefront_guidance(product: str = Query(..., description="Shopify numeric product id or PerkPilot product id")):
        """Public: merchant-approved fit guidance for the theme's product block (also mirrored to a product metafield)."""
        from .shopify_commerce import guidance_for_product

        pid = product if product.startswith("sfy_") or not product.isdigit() else f"sfy_{product}"
        return {"product_id": pid, "guidance": guidance_for_product(S(), pid)}

    # ------------------------------------------------------------ shopping

    @app.post("/api/intent/parse")
    def intent_parse(body: ParseRequest, request: Request):
        return shopping.parse_intent(S(), shopper_of(request, body.shopper_id), body.text)

    @app.post("/api/intent/confirm")
    def intent_confirm(body: ConfirmIntentRequest, request: Request):
        owned(request, shopping.get_intent(S(), body.intent_id)["shopper_id"])
        return shopping.confirm_intent(S(), body.intent_id, body.confirmed_requirements, body.change_reason)

    @app.get("/api/intent/{intent_id}")
    def intent_get(intent_id: str, request: Request):
        it = shopping.get_intent(S(), intent_id)
        owned(request, it["shopper_id"])
        return it

    @app.post("/api/recommendations")
    def recommendations(body: RecommendRequest, request: Request):
        owned(request, shopping.get_intent(S(), body.intent_id)["shopper_id"])
        return shopping.recommend(S(), body.intent_id, body.apply_learned_rules)

    @app.post("/api/checkout/verify")
    def checkout_verify(body: VerifyRequest, request: Request):
        owned(request, shopping.get_intent(S(), body.intent_id)["shopper_id"])
        return customer_quote(shopping.verify_proposal(S(), body.intent_id, body.proposal.model_dump()))

    @app.get("/api/checkout/quotes/{quote_id}")
    def quote_get(quote_id: str, request: Request):
        q = shopping.get_quote(S(), quote_id)
        owned(request, q["shopper_id"])
        intent = shopping.get_intent(S(), q["intent_id"])
        handoff = S().find_one("handoffs", {"quote_id": quote_id})
        return {"quote": customer_quote(q),
                "intent": {k: intent[k] for k in ("intent_id", "status", "confirmed_requirements", "confirmation_version")},
                "product": S().get("products", q["proposal"]["product_id"]),
                "payment_method": S().get("payment_methods", q["proposal"]["payment_method_id"]),
                "handoff": shopping.public_handoff(handoff), "platform": "shopify" if shopify_mode() else m}

    if m == "shopify":
        register_shopify_commerce_routes(app, S, shopper_of)

    # ------------------------------------------------------------ purchases

    @app.get("/api/purchases")
    def purchases(request: Request, shopper_id: str | None = Query(default=None)):
        s = S()
        sid = shopper_of(request, shopper_id)
        out = [{**customer_purchase(s, p), "claims": _claim_summaries(s, p["purchase_id"])}
               for p in s.find("purchases", {"shopper_id": sid}) if shopper_visible(p)]
        pending = [shopping.public_handoff(h) for h in s.find("handoffs", {"shopper_id": sid}) if h["status"] == "awaiting_payment"]
        return {"purchases": sorted(out, key=lambda p: p["created_at"], reverse=True), "awaiting_payment": pending}

    @app.get("/api/purchases/{purchase_id}")
    def purchase(purchase_id: str, request: Request, shopper_id: str | None = Query(default=None)):
        s = S()
        sid = shopper_of(request, shopper_id)
        p = s.get("purchases", purchase_id)
        if not p or p["shopper_id"] != sid or not shopper_visible(p):
            raise DomainError("Purchase not found for this shopper.", 404, "not_found")
        if shopify_mode():
            from .shopify_commerce import maybe_refresh

            p = maybe_refresh(s, p)
            for c in s.find("purchases", {"parent_purchase_id": purchase_id}):
                maybe_refresh(s, c)
        children = [c for c in s.find("purchases", {"parent_purchase_id": purchase_id}) if shopper_visible(c)]
        return {"purchase": customer_purchase(s, p), "claims": _claim_summaries(s, purchase_id),
                "exchange_orders": [customer_purchase(s, c) for c in children], "product": s.get("products", p["product_id"])}

    def _claim_summaries(s: BaseStore, purchase_id: str) -> list[dict]:
        return [{k: c[k] for k in ("claim_id", "status", "classification")} for c in s.find("claims", {"purchase_id": purchase_id})]

    # ------------------------------------------------------------ Shopify live data

    @app.post("/api/shopify/webhooks")
    async def shopify_webhook(request: Request, background: BackgroundTasks):
        # Public endpoint: the HMAC over the raw body, signed with the app's client secret, is the authentication.
        raw = await request.body()
        h = request.headers
        if not shopify_stream.verify_webhook(raw, h.get("x-shopify-hmac-sha256", ""), os.environ.get("SHOPIFY_CLIENT_SECRET", "")):
            raise DomainError("Webhook signature is invalid.", 401, "invalid_signature")
        if h.get("x-shopify-shop-domain") and h["x-shopify-shop-domain"] != shopify_stream.shop_domain():
            raise DomainError("Webhook is for a different shop.", 403, "wrong_shop")
        payload = json.loads(raw or b"{}")
        result = shopify_stream.handle_webhook(S(), h.get("x-shopify-topic", ""), h.get("x-shopify-webhook-id", ""), payload)
        if result in ("catalog", "discounts") or (shopify_mode() and result in ("order", "order_child")):
            background.add_task(shopify_stream.follow_up, S(), result, payload)  # ack within Shopify's 5 s; work afterwards
        return {"ok": True, "result": result}

    @app.get("/api/admin/shopify", dependencies=[Depends(require_admin)])
    def shopify_overview():
        return shopify_stream.overview(S())

    @app.post("/api/admin/shopify/connect", dependencies=[Depends(require_admin)])
    def shopify_connect():
        # The callback comes only from server config, so a caller can't point the store's webhooks elsewhere.
        public = os.environ.get("PERKPILOT_PUBLIC_URL", "").strip()
        if not shopify_stream.configured():
            raise DomainError("Shopify isn't configured. Set SHOPIFY_SHOP and the app credentials in backend/.env.", 422, "shopify_not_configured")
        if not public.startswith("https://"):
            raise DomainError("Set PERKPILOT_PUBLIC_URL in backend/.env to the public HTTPS address of this app (e.g. your ngrok URL).",
                              422, "public_url_missing")
        try:
            out = shopify_stream.connect(S(), public)
            if shopify_mode():
                _store_name(S())
            return out
        except (httpx.HTTPError, RuntimeError) as exc:
            raise DomainError(f"Shopify request failed: {exc}", 502, "shopify_error")

    # ------------------------------------------------------------ admin audit (merchant auth in every mode)

    @app.get("/api/admin/purchases", dependencies=[Depends(require_admin)])
    def admin_purchases():
        s = S()
        rows = [{**{k: p.get(k) for k in ("purchase_id", "kind", "shopper_id", "product_name", "approved_charge_cents", "status",
                                          "source", "created_at", "shopify_order_name", "shopify_test")}, "verified": is_verified(s, p)}
                for p in s.find("purchases") if p.get("source") != "seed" or demo_mode]
        return {"purchases": sorted(rows, key=lambda p: p["created_at"], reverse=True),
                "awaiting_payment": [shopping.public_handoff(h) | {"product_id": h["product_id"]} for h in s.find("handoffs")
                                     if h["status"] == "awaiting_payment"]}

    @app.get("/api/admin/purchases/{purchase_id}/audit", dependencies=[Depends(require_admin)])
    def admin_purchase_audit(purchase_id: str):
        s = S()
        p = s.get("purchases", purchase_id)
        if not p:
            raise DomainError("Unknown purchase.", 404, "not_found")
        if shopify_mode():
            from .shopify_commerce import maybe_refresh

            p = maybe_refresh(s, p, max_age_s=5)
        return {"purchase": p, "integrity": integrity(s, p), "verified": is_verified(s, p),
                "verification_results": p["verification_results"],
                "events": sorted(s.find("events", {"purchase_id": purchase_id}), key=lambda e: e["seq"]),
                "shipment": shipment_for(s, purchase_id), "claims": s.find("claims", {"purchase_id": purchase_id}),
                "intent": s.get("intents", p["intent_id"]) if p.get("intent_id") else None,
                "quote": s.get("quotes", p["quote_id"]) if p.get("quote_id") else None,
                "shopify_admin_url": _admin_order_url(p.get("shopify_order_id"))}

    # ------------------------------------------------------------ claims / remedies

    @app.post("/api/claims")
    def claim_create(body: ClaimRequest, request: Request):
        sid = shopper_of(request, body.shopper_id)
        if shopify_mode() and (p := S().get("purchases", body.purchase_id)) and p["shopper_id"] == sid:
            from .shopify_commerce import maybe_refresh

            maybe_refresh(S(), p, max_age_s=0)  # classify against Shopify's current fulfillment and payment evidence
        c = resolution.create_claim(S(), body.purchase_id, sid, body.complaint_text)
        return resolution.claim_view(S(), c["claim_id"])

    @app.get("/api/claims/{claim_id}")
    def claim_get(claim_id: str, request: Request):
        c = resolution._claim(S(), claim_id)
        owned(request, c["shopper_id"])
        if shopify_mode():
            _refresh_claim_orders(S(), c)
        return resolution.claim_view(S(), claim_id)

    @app.post("/api/claims/{claim_id}/clarify")
    def claim_clarify(claim_id: str, body: ClarifyRequest, request: Request):
        owned(request, resolution._claim(S(), claim_id)["shopper_id"])
        resolution.clarify_claim(S(), claim_id, body.text)
        return resolution.claim_view(S(), claim_id)

    @app.get("/api/claims/{claim_id}/remedies")
    def claim_remedies(claim_id: str, request: Request):
        owned(request, resolution._claim(S(), claim_id)["shopper_id"])
        return {"remedies": resolution.generate_remedies(S(), claim_id)}

    @app.post("/api/claims/{claim_id}/outcome")
    def claim_outcome(claim_id: str, body: OutcomeRequest, request: Request):
        sid = shopper_of(request, body.shopper_id)
        if shopify_mode():
            _refresh_claim_orders(S(), resolution._claim(S(), claim_id))
        res = resolution.confirm_outcome(S(), claim_id, sid, body.outcome)
        return {**resolution.claim_view(S(), claim_id), "rule_proposal": res["rule_proposal"]}

    # ------------------------------------------------------------ merchant

    @app.get("/api/merchant/rules")
    def rules():
        return learning.rules_overview(S())

    @app.post("/api/merchant/rules/{rule_id}/approve")
    def rule_approve(rule_id: str, body: RuleDecisionRequest, request: Request):
        return learning.approve_rule(S(), rule_id, merchant(request, body.actor), body.note)

    @app.post("/api/merchant/rules/{rule_id}/reject")
    def rule_reject(rule_id: str, body: RuleDecisionRequest, request: Request):
        return learning.reject_rule(S(), rule_id, merchant(request, body.actor), body.note)

    @app.post("/api/merchant/rules/{rule_id}/toggle")
    def rule_toggle(rule_id: str, body: RuleToggleRequest, request: Request):
        return learning.toggle_rule(S(), rule_id, body.enabled, merchant(request, body.actor))

    @app.get("/api/merchant/dashboard")
    def dashboard():
        return metrics.dashboard(S())

    @app.get("/api/merchant/claims")
    def merchant_claims():
        cs = sorted(S().find("claims", {"merchant_id": MERCHANT_ID}), key=lambda c: c["created_at"], reverse=True)
        if not demo_mode:
            cs = [c for c in cs if c.get("source") != "seed"]
        for c in cs:
            if c.get("accepted_remedy_id"):
                c["accepted_remedy"] = S().get("remedies", c["accepted_remedy_id"])
        return {"claims": cs}

    @app.get("/api/merchant/settings")
    def settings_get():
        from .shopify_commerce import SETTINGS_KEYS

        pol = S().get("policies", POLICY_ID)
        return {"settings": {k: pol.get(k) for k in SETTINGS_KEYS}, "mode": m}

    @app.post("/api/eval/run")
    def eval_run():
        return run_evaluation(S())

    @app.get("/api/eval/runs/latest")
    def eval_latest():
        runs = sorted(S().find("eval_runs"), key=lambda r: r["timestamp"], reverse=True)
        return {"run": runs[0] if runs else None}

    return app


def _initial_sync(store: BaseStore) -> None:
    """First boot in Shopify mode: pull the catalog and discount codes so the assistant has something to sell."""
    try:
        shopify_stream.resync_catalog(store)
        _store_name(store)
        from .shopify_commerce import sync_discounts

        sync_discounts(store)
    except Exception as exc:  # noqa: BLE001 - surfaced on the dashboard; the merchant can press Connect store
        shopify_stream._log(store, "error", f"Initial Shopify sync failed: {exc}")


def _store_name(store: BaseStore) -> None:
    try:
        shop = shopify_api.gql(shopify_api.SHOP)["shop"]
    except shopify_api.ShopifyError:
        return
    m = store.get("merchants", MERCHANT_ID)
    if m and not os.environ.get("PERKPILOT_STORE_NAME"):
        store.put("merchants", {**m, "name": shop["name"]})


def _admin_order_url(order_gid: str | None) -> str | None:
    if not order_gid:
        return None
    return f"https://admin.shopify.com/store/{shopify_api.shop_domain().removesuffix('.myshopify.com')}/orders/{order_gid.rsplit('/', 1)[-1]}"


def _refresh_claim_orders(store: BaseStore, claim: dict) -> None:
    """Exchange replacements are Shopify orders: re-read them so a delivery shows without waiting for a webhook."""
    from .shopify_commerce import maybe_refresh

    for p in [store.get("purchases", claim["purchase_id"])] + store.find("purchases", {"parent_purchase_id": claim["purchase_id"]}):
        if p:
            maybe_refresh(store, p)


def register_shopify_commerce_routes(app: FastAPI, S, shopper_of) -> None:
    """Checkout handoff, remedies, fulfillment and pilot setup against the merchant's Shopify store."""
    from . import shopify_commerce as sc

    @app.post("/api/checkout/confirm")
    def checkout_confirm(body: CheckoutConfirmRequest, request: Request):
        sid = shopper_of(request, body.shopper_id)
        res = shopping.confirm_checkout(S(), body.quote_id, sid, body.approved_charge_cents, body.shopper_approved)
        return {**res, "purchase": customer_purchase(S(), res["purchase"]) if res.get("purchase") else None}

    @app.get("/api/checkout/handoffs/{purchase_id}")
    def handoff_get(purchase_id: str, request: Request):
        h = S().get("handoffs", purchase_id)
        if not h or h["shopper_id"] != shopper_of(request):
            raise DomainError("Unknown checkout.", 404, "not_found")
        try:
            h = sc.refresh_handoff(S(), purchase_id)
        except shopify_api.ShopifyError:
            pass  # show the last known state; the next poll retries
        p = S().get("purchases", purchase_id)
        return {"handoff": shopping.public_handoff(h), "purchase": customer_purchase(S(), p) if p else None}

    @app.post("/api/remedies/{remedy_id}/accept")
    def remedy_accept(remedy_id: str, body: RemedyAcceptRequest, request: Request):
        sid = shopper_of(request, body.shopper_id)
        res = resolution.accept_remedy(S(), remedy_id, sid, body.confirm, body.idempotency_key, body.payout_method)
        return {**resolution.claim_view(S(), res["claim"]["claim_id"]), "duplicate": res["duplicate"], "message": res.get("message")}

    @app.post("/api/merchant/remedies/{remedy_id}/execute")
    def remedy_execute(remedy_id: str, request: Request):
        res = resolution.merchant_execute(S(), remedy_id, getattr(request.state, "merchant_actor", "merchant"))
        return {**resolution.claim_view(S(), res["claim"]["claim_id"]), "duplicate": res["duplicate"]}

    @app.post("/api/merchant/purchases/{purchase_id}/ship-and-deliver")
    def ship_and_deliver(purchase_id: str):
        p = S().get("purchases", purchase_id)
        if not p or not p.get("shopify_order_id"):
            raise DomainError("Unknown Shopify purchase.", 404, "not_found")
        try:
            return {"purchase": customer_purchase(S(), sc.ship_and_deliver(S(), p))}
        except shopify_api.ShopifyError as exc:
            raise DomainError(f"Shopify refused the fulfillment: {exc}", 502, "shopify_error")

    @app.post("/api/merchant/purchases/{purchase_id}/refresh")
    def purchase_refresh(purchase_id: str):
        p = S().get("purchases", purchase_id)
        if not p or not p.get("shopify_order_id"):
            raise DomainError("Unknown Shopify purchase.", 404, "not_found")
        try:
            return {"purchase": customer_purchase(S(), sc.reconcile_order(S(), p["shopify_order_id"]))}
        except shopify_api.ShopifyError as exc:
            raise DomainError(str(exc), 502, "shopify_error")

    @app.post("/api/merchant/settings")
    def settings_update(body: SettingsPatch):
        return {"settings": sc.update_settings(S(), body.model_dump(exclude_none=True)), "mode": "shopify"}

    @app.post("/api/merchant/setup/scenario")
    def setup_scenario(body: ScenarioRequest):
        try:
            return sc.prepare_scenario(S(), body.product_id, body.variant_id)
        except shopify_api.ShopifyError as exc:
            raise DomainError(f"Shopify refused the setup: {exc}", 502, "shopify_error")

    @app.post("/api/merchant/setup/sample-history")
    def setup_history(body: HistoryRequest):
        return sc.seed_fit_history(S(), body.product_id, body.size, body.to_size)

    @app.post("/api/merchant/setup/sync-discounts")
    def setup_discounts():
        try:
            return {"discounts": sc.sync_discounts(S())}
        except shopify_api.ShopifyError as exc:
            raise DomainError(str(exc), 502, "shopify_error")

    @app.get("/api/merchant/catalog")
    def merchant_catalog():
        prods = [{"product_id": p["product_id"], "name": p["name"], "category": p["category"],
                  "variants": [{"variant_id": v["variant_id"], "color": v["color"], "size": v["size"], "price_cents": v.get("price_cents"),
                                "stock": p["inventory_by_variant"].get(v["variant_id"], 0)} for v in p["variants"]],
                  "sizes": sizes_in_order(p)}
                 for p in S().find("products", {"status": "active", "source": "shopify"})]
        return {"products": sorted(prods, key=lambda p: p["name"]),
                "offers": [o for o in S().find("offers") if o["status"] == "active"]}


def register_demo_routes(app: FastAPI, S) -> None:
    """Reset/clock/price scenarios plus simulated checkout payment and remedy execution."""

    @app.get("/api/demo/state")
    def demo_state():
        s = S()
        return {"now": now(s).isoformat(), "timezone": "America/Los_Angeles", "store": s.mode, "ai": ai_status(),
                "merchant": s.get("merchants", MERCHANT_ID), "shoppers": s.find("shoppers"),
                "payment_methods": s.find("payment_methods"),
                "next_delivery_at": (nd.isoformat() if (nd := next_delivery_time(s)) else None),
                "disclosure": "Simulated store, shoppers, payments, refunds and shipping. No real money moves."}

    @app.post("/api/demo/reset")
    def demo_reset():
        return demo.reset(S())

    @app.post("/api/demo/advance")
    def demo_advance(body: AdvanceRequest):
        return demo.advance(S(), body.days, body.hours, body.until, body.purchase_id)

    @app.post("/api/merchant/products/{product_id}/price")
    def change_price(product_id: str, body: PriceChange):
        return demo.change_price(S(), product_id, body.price_cents)

    @app.post("/api/checkout/confirm")
    def checkout_confirm(body: CheckoutConfirmRequest):
        if not body.shopper_id:
            raise DomainError("shopper_id is required.", 422, "invalid_request")
        res = shopping.confirm_checkout(S(), body.quote_id, body.shopper_id, body.approved_charge_cents, body.shopper_approved)
        return {**res, "purchase": customer_purchase(S(), res["purchase"]) if res["purchase"] else None}

    @app.post("/api/remedies/{remedy_id}/accept")
    def remedy_accept(remedy_id: str, body: RemedyAcceptRequest):
        if not body.shopper_id:
            raise DomainError("shopper_id is required.", 422, "invalid_request")
        res = resolution.accept_remedy(S(), remedy_id, body.shopper_id, body.confirm, body.idempotency_key, body.payout_method)
        return {**resolution.claim_view(S(), res["claim"]["claim_id"]), "duplicate": res["duplicate"], "message": res.get("message")}


app = create_app()
