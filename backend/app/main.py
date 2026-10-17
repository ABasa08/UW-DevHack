"""PerkPilot FastAPI application."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import demo, learning, metrics, resolution, shopping
from .ai import ai_status
from .core import MERCHANT_ID, DomainError, now
from .evaluation import run_evaluation
from .fixtures import seed
from .models import (AdvanceRequest, CheckoutConfirmRequest, ClaimRequest, ClarifyRequest, ConfirmIntentRequest, OutcomeRequest,
                     ParseRequest, RecommendRequest, RemedyAcceptRequest, RuleDecisionRequest, RuleToggleRequest, VerifyRequest)
from .records import integrity, shipment_for
from .store import BaseStore, create_store
from .verifier import active_rules_for


def _load_env() -> None:
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class PriceChange(BaseModel):
    price_cents: int = Field(ge=100, le=1_000_00)


def create_app(store: BaseStore | None = None) -> FastAPI:
    _load_env()
    app = FastAPI(title="PerkPilot API", version="1.0.0",
                  description="Verified AI commerce demo. All payments, refunds and shipments are simulated.")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.state.store = store or create_store()
    if not app.state.store.get("meta", "seed_info"):
        seed(app.state.store)

    def S() -> BaseStore:
        return app.state.store

    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message, "detail": exc.detail}})

    # ------------------------------------------------------------ meta / demo

    @app.get("/api/health")
    def health():
        return {"ok": True, "store": S().mode, "ai": ai_status(), "now": now(S()).isoformat()}

    @app.get("/api/demo/state")
    def demo_state():
        s = S()
        return {"now": now(s).isoformat(), "timezone": "America/Los_Angeles", "store": s.mode, "ai": ai_status(),
                "merchant": s.get("merchants", MERCHANT_ID), "shoppers": s.find("shoppers"),
                "payment_methods": s.find("payment_methods"),
                "disclosure": "Simulated store, shoppers, payments, refunds and shipping. No real money moves."}

    @app.post("/api/demo/reset")
    def demo_reset():
        return demo.reset(S())

    @app.post("/api/demo/advance")
    def demo_advance(body: AdvanceRequest):
        return demo.advance(S(), body.days, body.hours, body.until, body.purchase_id)

    # ------------------------------------------------------------ catalog

    @app.get("/api/products")
    def products(category: str | None = None):
        items = S().find("products", {"merchant_id": MERCHANT_ID, "status": "active"})
        if category:
            items = [p for p in items if p["category"] == category]
        return {"products": sorted(items, key=lambda p: (p["category"] != "running_shoes", p["name"]))}

    @app.get("/api/products/{product_id}")
    def product(product_id: str):
        p = S().get("products", product_id)
        if not p:
            raise DomainError("Unknown product.", 404, "not_found")
        sizes = sorted({v["size"] for v in p["variants"]}, key=float)
        guidance = {size: [{"rule_id": r["rule_id"], "message": r["customer_message"], "suggest_size": r["proposed_action"]["suggest_size"]}
                           for r in active_rules_for(S(), product_id, size)] for size in sizes}
        offers = [o for o in S().find("offers") if o["status"] == "active" and (not o["eligible_products"] or product_id in o["eligible_products"])]
        return {"product": p, "guidance_by_size": {k: v for k, v in guidance.items() if v}, "offers": offers}

    @app.post("/api/merchant/products/{product_id}/price")
    def change_price(product_id: str, body: PriceChange):
        return demo.change_price(S(), product_id, body.price_cents)

    # ------------------------------------------------------------ shopping

    @app.post("/api/intent/parse")
    def intent_parse(body: ParseRequest):
        return shopping.parse_intent(S(), body.shopper_id, body.text)

    @app.post("/api/intent/confirm")
    def intent_confirm(body: ConfirmIntentRequest):
        return shopping.confirm_intent(S(), body.intent_id, body.confirmed_requirements, body.change_reason)

    @app.get("/api/intent/{intent_id}")
    def intent_get(intent_id: str):
        return shopping.get_intent(S(), intent_id)

    @app.post("/api/recommendations")
    def recommendations(body: RecommendRequest):
        return shopping.recommend(S(), body.intent_id, body.apply_learned_rules)

    @app.post("/api/checkout/verify")
    def checkout_verify(body: VerifyRequest):
        return shopping.verify_proposal(S(), body.intent_id, body.proposal.model_dump())

    @app.get("/api/checkout/quotes/{quote_id}")
    def quote_get(quote_id: str):
        q = shopping.get_quote(S(), quote_id)
        return {"quote": q, "intent": shopping.get_intent(S(), q["intent_id"]),
                "product": S().get("products", q["proposal"]["product_id"]),
                "payment_method": S().get("payment_methods", q["proposal"]["payment_method_id"])}

    @app.post("/api/checkout/confirm")
    def checkout_confirm(body: CheckoutConfirmRequest):
        return shopping.confirm_checkout(S(), body.quote_id, body.shopper_id, body.approved_charge_cents, body.shopper_approved)

    # ------------------------------------------------------------ purchases

    @app.get("/api/purchases")
    def purchases(shopper_id: str = Query(...)):
        items = S().find("purchases", {"shopper_id": shopper_id})
        out = []
        for p in items:
            sh = shipment_for(S(), p["purchase_id"])
            claims = S().find("claims", {"purchase_id": p["purchase_id"]})
            out.append({**p, "shipment": sh, "claims": [{"claim_id": c["claim_id"], "status": c["status"],
                                                        "classification": c["classification"]} for c in claims]})
        return {"purchases": sorted(out, key=lambda p: p["created_at"], reverse=True)}

    @app.get("/api/purchases/{purchase_id}")
    def purchase(purchase_id: str, shopper_id: str | None = None):
        s = S()
        p = s.get("purchases", purchase_id)
        if not p or (shopper_id and p["shopper_id"] != shopper_id):
            raise DomainError("Purchase not found for this shopper.", 404, "not_found")
        events = sorted(s.find("events", {"purchase_id": purchase_id}), key=lambda e: e["seq"])
        claims = s.find("claims", {"purchase_id": purchase_id})
        children = s.find("purchases", {"parent_purchase_id": purchase_id})
        return {"purchase": p, "shipment": shipment_for(s, purchase_id), "events": events, "integrity": integrity(s, p),
                "claims": claims, "exchange_orders": [{**c, "shipment": shipment_for(s, c["purchase_id"])} for c in children],
                "product": s.get("products", p["product_id"]), "intent": s.get("intents", p["intent_id"]) if p.get("intent_id") else None}

    # ------------------------------------------------------------ claims / remedies

    @app.post("/api/claims")
    def claim_create(body: ClaimRequest):
        c = resolution.create_claim(S(), body.purchase_id, body.shopper_id, body.complaint_text)
        return resolution.claim_view(S(), c["claim_id"])

    @app.get("/api/claims/{claim_id}")
    def claim_get(claim_id: str):
        return resolution.claim_view(S(), claim_id)

    @app.post("/api/claims/{claim_id}/clarify")
    def claim_clarify(claim_id: str, body: ClarifyRequest):
        resolution.clarify_claim(S(), claim_id, body.text)
        return resolution.claim_view(S(), claim_id)

    @app.get("/api/claims/{claim_id}/remedies")
    def claim_remedies(claim_id: str):
        return {"remedies": resolution.generate_remedies(S(), claim_id)}

    @app.post("/api/claims/{claim_id}/outcome")
    def claim_outcome(claim_id: str, body: OutcomeRequest):
        res = resolution.confirm_outcome(S(), claim_id, body.shopper_id, body.outcome)
        return {**resolution.claim_view(S(), claim_id), "rule_proposal": res["rule_proposal"]}

    @app.post("/api/remedies/{remedy_id}/accept")
    def remedy_accept(remedy_id: str, body: RemedyAcceptRequest):
        res = resolution.accept_remedy(S(), remedy_id, body.shopper_id, body.confirm, body.idempotency_key, body.payout_method)
        return {**resolution.claim_view(S(), res["claim"]["claim_id"]), "duplicate": res["duplicate"], "message": res.get("message")}

    # ------------------------------------------------------------ merchant

    @app.get("/api/merchant/rules")
    def rules():
        return learning.rules_overview(S())

    @app.post("/api/merchant/rules/{rule_id}/approve")
    def rule_approve(rule_id: str, body: RuleDecisionRequest):
        return learning.approve_rule(S(), rule_id, body.actor, body.note)

    @app.post("/api/merchant/rules/{rule_id}/reject")
    def rule_reject(rule_id: str, body: RuleDecisionRequest):
        return learning.reject_rule(S(), rule_id, body.actor, body.note)

    @app.post("/api/merchant/rules/{rule_id}/toggle")
    def rule_toggle(rule_id: str, body: RuleToggleRequest):
        return learning.toggle_rule(S(), rule_id, body.enabled, body.actor)

    @app.get("/api/merchant/dashboard")
    def dashboard():
        return metrics.dashboard(S())

    @app.get("/api/merchant/claims")
    def merchant_claims():
        cs = sorted(S().find("claims", {"merchant_id": MERCHANT_ID}), key=lambda c: c["created_at"], reverse=True)
        return {"claims": cs}

    @app.post("/api/eval/run")
    def eval_run():
        return run_evaluation(S())

    @app.get("/api/eval/runs/latest")
    def eval_latest():
        runs = sorted(S().find("eval_runs"), key=lambda r: r["timestamp"], reverse=True)
        return {"run": runs[0] if runs else None}

    return app


app = create_app()
