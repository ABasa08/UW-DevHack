"""PERKPILOT_MODE=shopify end to end, against an in-memory Shopify (tests/fake_shopify.py).

Covers the spec's judge flow on a Shopify catalog: the agent's card-offer mistake is blocked using Shopify's own totals,
the approved quote becomes a Shopify draft order, the purchase record is built from the paid Shopify order, a fit
complaint becomes a real replacement order, the confirmed outcome proposes a rule, and the approved rule is written to
the product's metafield and shown to the next shopper.
"""

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import auth, shopify_api, shopify_commerce
from app.main import create_app
from app.shopify import sync_catalog
from app.store import MemoryStore

from .conftest import ok
from .fake_shopify import FakeShopify

ADMIN = {"Authorization": "Bearer adm"}
HOODIE = (5, "Heavyweight Hoodie", "Hoodie", [(51, "S", "Black", 60_00, 5), (52, "M", "Black", 60_00, 5), (53, "L", "Black", 60_00, 5)])


@pytest.fixture()
def shop(monkeypatch):
    monkeypatch.setenv("PERKPILOT_MODE", "shopify")
    monkeypatch.setenv("SHOPIFY_SHOP", "acme.myshopify.com")
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "sec")
    monkeypatch.setenv("PERKPILOT_ADMIN_TOKEN", "adm")
    monkeypatch.delenv("SHOPIFY_ADMIN_TOKEN", raising=False)
    fake = FakeShopify()
    monkeypatch.setattr(shopify_api, "_transport", fake.transport())
    shopify_api._token.clear()
    shopify_commerce._calc_cache.clear()
    store = MemoryStore()
    client = TestClient(create_app(store))
    sync_catalog(store, fake.product_nodes([HOODIE]), shop="acme.myshopify.com")
    shopify_commerce.apply_settings(store)
    return client, store, fake


def session(client) -> dict:
    s = ok(client.post("/api/session/anonymous", json={}))
    return {"Authorization": f"Bearer {s['token']}"}


def prepared(client):
    return ok(client.post("/api/merchant/setup/scenario", json={"product_id": "sfy_5", "variant_id": "sfy_5-52"}, headers=ADMIN))


def shop_until_quote(client, who, prompt):
    it = ok(client.post("/api/intent/parse", json={"text": prompt}, headers=who))
    it = ok(client.post("/api/intent/confirm", json={"intent_id": it["intent_id"], "confirmed_requirements": it["parsed_requirements"]},
                        headers=who))
    recs = ok(client.post("/api/recommendations", json={"intent_id": it["intent_id"]}, headers=who))
    return it, recs


def buy(client, fake, who, prompt, **pay):
    it, recs = shop_until_quote(client, who, prompt)
    blocked = ok(client.post("/api/checkout/verify", json={"intent_id": it["intent_id"], "proposal": recs["agent_proposal"]}, headers=who))
    option = next(o for o in blocked["result"]["options"] if o["option_id"] == blocked["result"]["recommended_option"])
    q = ok(client.post("/api/checkout/verify", json={"intent_id": it["intent_id"], "proposal": {**option["proposal"], "source": "corrected_option"}},
                       headers=who))
    out = ok(client.post("/api/checkout/confirm", json={"quote_id": q["quote_id"], "approved_charge_cents": q["result"]["price_breakdown"]["charge_cents"],
                                                        "shopper_approved": True}, headers=who))
    draft = next(g for g, dr in fake.drafts.items() if any(a["value"] == out["handoff"]["purchase_id"] for a in dr["input"].get("customAttributes", [])))
    fake.pay(draft, **pay)
    done = ok(client.get(f"/api/checkout/handoffs/{out['handoff']['purchase_id']}", headers=who))
    return blocked, q, out, done


def test_judge_flow_runs_on_shopify(shop):
    client, store, fake = shop
    scen = prepared(client)
    assert scen["coupon_code"] == "PERKPILOT9" and "PERKPILOT9" in fake.codes
    jordan = session(client)
    blocked, q, out, done = buy(client, fake, jordan, scen["shopper_prompt"])

    # Scene 1: the agent claims the card credit; Shopify's real totals prove the coupon pushed the charge under the threshold.
    fails = {c["rule"] for c in blocked["result"]["checks"] if c["status"] == "FAIL"}
    assert blocked["result"]["status"] == "FAIL" and "CARD_OFFER_ELIGIBILITY" in fails
    bd = q["result"]["price_breakdown"]
    assert q["result"]["status"] == "PASS" and bd["tax_source"] == "shopify" and bd["tax_cents"] == 2_55
    assert bd["charge_cents"] == scen["charge_with_coupon_cents"] == 61_55 <= scen["budget_cents"]
    assert any(c["rule"] == "SHOPIFY_TOTAL_MATCH" and c["status"] == "PASS" for c in q["result"]["checks"])

    # Scene 2: approval becomes an exact Shopify draft order; the record is built from the paid order.
    h = out["handoff"]
    assert out["status"] == "AWAITING_PAYMENT" and h["checkout_url"].startswith("https://acme.myshopify.com/")
    [(draft_gid, draft)] = [(g, dr) for g, dr in fake.drafts.items()]
    assert draft["input"]["discountCodes"] == ["PERKPILOT9"] and draft["input"]["reserveInventoryUntil"]
    p = done["purchase"]
    assert done["handoff"]["status"] == "paid" and p["verification"]["verified"]
    assert p["payment"]["method"] == "Visa •••• 4242 (Shopify test payment)" and not p["payment"]["simulated"]
    pid = p["purchase_id"]
    assert ok(client.get("/api/purchases", headers=jordan))["purchases"][0]["purchase_id"] == pid
    assert client.get(f"/api/purchases/{pid}", headers=session(client)).status_code == 404  # another shopper

    # Scene 3: delivered through a real fulfillment event, then a fit complaint against the record.
    assert client.post(f"/api/merchant/purchases/{pid}/ship-and-deliver").status_code == 401
    assert ok(client.post(f"/api/merchant/purchases/{pid}/ship-and-deliver", headers=ADMIN))["purchase"]["status"] == "delivered"
    ok(client.post("/api/merchant/setup/sample-history", json={"product_id": "sfy_5", "size": "M", "to_size": "L"}, headers=ADMIN))
    claim = ok(client.post("/api/claims", json={"purchase_id": pid, "complaint_text": "These feel too tight."}, headers=jordan))
    assert claim["claim"]["classification"] == "SUSPECTED_PRODUCT_FIT" and claim["claim"]["prior_validated_outcomes"] == 2
    ex = next(r for r in claim["remedies"] if r["remedy_type"] == "exchange" and r["status"] == "eligible")
    assert ex["target_variant_id"] == "sfy_5-53"
    claim = ok(client.post(f"/api/remedies/{ex['remedy_id']}/accept", json={"confirm": True}, headers=jordan))
    assert claim["claim"]["status"] == "exchange_in_progress"
    repl = store.get("purchases", claim["exchange"]["exchange_purchase_id"])
    assert repl["shopify_order_id"] in fake.orders and "perkpilot-exchange" in fake.orders[p and store.get("purchases", pid)["shopify_order_id"]]["tags"]
    ok(client.post(f"/api/merchant/purchases/{repl['purchase_id']}/ship-and-deliver", headers=ADMIN))

    # Scene 4: the confirmed exchange is the third outcome; the merchant approves; Shopify gets the guidance.
    res = ok(client.post(f"/api/claims/{claim['claim']['claim_id']}/outcome", json={"outcome": "fits"}, headers=jordan))
    rule = res["rule_proposal"]
    assert rule["status"] == "proposed" and rule["confirmed_outcome_count"] == 3 and rule["affected_order_count"] == 21
    assert "2 of the confirmed outcomes are labeled synthetic" in " ".join(rule["limitations"])
    approved = ok(client.post(f"/api/merchant/rules/{rule['rule_id']}/approve", json={}, headers=ADMIN))
    assert approved["approval_actor"] == "admin_token" and approved["storefront_sync"]["synced"]
    assert fake.metafields["gid://shopify/Product/5"]["M"]["suggest_size"] == "L"

    # Scene 5: the next shopper asking for size M sees the approved guidance, in the assistant and on the product page.
    _, recs = shop_until_quote(client, session(client), scen["shopper_prompt"])
    assert recs["guidance"] and recs["guidance"][0]["suggest_size"] == "L"
    assert ok(client.get("/api/storefront/guidance", params={"product": "5"}))["guidance"]["M"]["suggest_size"] == "L"
    dash = ok(client.get("/api/merchant/dashboard", headers=ADMIN))["session_metrics"]
    assert dash["proposals_blocked"] >= 1 and dash["orders_confirmed"] == 1 and dash["exchanges_completed"] == 1 and dash["rules_approved"] == 1


def test_refunds_run_once_through_shopify_and_real_orders_wait_for_the_merchant(shop):
    client, store, fake = shop
    scen = prepared(client)
    who = session(client)
    *_, done = buy(client, fake, who, scen["shopper_prompt"])
    pid = done["purchase"]["purchase_id"]
    ok(client.post(f"/api/merchant/purchases/{pid}/ship-and-deliver", headers=ADMIN))
    claim = ok(client.post("/api/claims", json={"purchase_id": pid, "complaint_text": "I changed my mind, I'd rather return it."}, headers=who))
    refund = next(r for r in claim["remedies"] if r["remedy_type"] == "refund")
    first = ok(client.post(f"/api/remedies/{refund['remedy_id']}/accept", json={"confirm": True}, headers=who))
    again = ok(client.post(f"/api/remedies/{refund['remedy_id']}/accept", json={"confirm": True}, headers=who))
    order = fake.orders[store.get("purchases", pid)["shopify_order_id"]]
    assert first["claim"]["status"] == "resolved" and again["duplicate"] and len(order["refunds"]) == 1
    assert c_amount(order["refunds"][0]) == refund["amount_cents"] and f"PerkPilot remedy {refund['remedy_id']}" in order["refunds"][0]["note"]

    # A real (non-test) order: the shopper's choice is held until the merchant approves it.
    who2 = session(client)
    *_, done2 = buy(client, fake, who2, scen["shopper_prompt"], test=False)
    pid2 = done2["purchase"]["purchase_id"]
    assert client.post(f"/api/merchant/purchases/{pid2}/ship-and-deliver", headers=ADMIN).status_code == 409  # real orders ship in Shopify
    fake._fulfill(order2 := fake.orders[store.get("purchases", pid2)["shopify_order_id"]])
    fake._deliver(order2)
    claim2 = ok(client.post("/api/claims", json={"purchase_id": pid2, "complaint_text": "I changed my mind."}, headers=who2))
    r2 = next(r for r in claim2["remedies"] if r["remedy_type"] == "refund")
    held = ok(client.post(f"/api/remedies/{r2['remedy_id']}/accept", json={"confirm": True}, headers=who2))
    assert held["claim"]["status"] == "awaiting_merchant" and not order2["refunds"]
    assert client.post(f"/api/merchant/remedies/{r2['remedy_id']}/execute").status_code == 401
    done = ok(client.post(f"/api/merchant/remedies/{r2['remedy_id']}/execute", headers=ADMIN))
    assert done["claim"]["status"] == "resolved" and len(order2["refunds"]) == 1


def c_amount(refund):
    return int(round(float(refund["totalRefundedSet"]["shopMoney"]["amount"]) * 100))


def test_shopify_disagreeing_with_the_approval_is_caught(shop):
    client, store, fake = shop
    scen = prepared(client)
    who = session(client)
    it, recs = shop_until_quote(client, who, scen["shopper_prompt"])
    blocked = ok(client.post("/api/checkout/verify", json={"intent_id": it["intent_id"], "proposal": recs["agent_proposal"]}, headers=who))
    option = next(o for o in blocked["result"]["options"] if o["option_id"] == blocked["result"]["recommended_option"])
    q = ok(client.post("/api/checkout/verify", json={"intent_id": it["intent_id"], "proposal": option["proposal"]}, headers=who))
    # The merchant raises the price in Shopify after the shopper's quote: Shopify's draft total no longer matches.
    fake.variants["gid://shopify/ProductVariant/52"]["price_cents"] = 64_00
    shopify_commerce._calc_cache.clear()
    r = client.post("/api/checkout/confirm", json={"quote_id": q["quote_id"], "approved_charge_cents": q["result"]["price_breakdown"]["charge_cents"],
                                                   "shopper_approved": True}, headers=who)
    assert r.status_code == 409 and r.json()["error"]["code"] in ("NEEDS_RECONFIRMATION", "verification_failed")
    fake.variants["gid://shopify/ProductVariant/52"]["price_cents"] = 60_00
    shopify_commerce._calc_cache.clear()

    # Tax changes once the shopper enters an address at checkout: paid total != approval -> not verified, and a charge
    # complaint is classified against Shopify's payment record.
    fake.charge_extra_cents = 1_20
    *_, done = buy(client, fake, session(client) if False else who, scen["shopper_prompt"])
    p = done["purchase"]
    assert not p["verification"]["verified"]
    audit = ok(client.get(f"/api/admin/purchases/{p['purchase_id']}/audit", headers=ADMIN))
    check = next(c for c in audit["verification_results"] if c["rule"] == "ORDER_TOTAL_MATCHES_APPROVAL")
    assert check["status"] == "FAIL" and audit["shopify_admin_url"].startswith("https://admin.shopify.com/store/acme/orders/")
    claim = ok(client.post("/api/claims", json={"purchase_id": p["purchase_id"], "complaint_text": "I was charged more than I approved."}, headers=who))
    assert claim["claim"]["classification"] == "PRICE_OR_DELIVERY_MISMATCH" and claim["claim"]["overcharge_cents"] == 1_20
    assert claim["remedies"][0]["remedy_type"] == "partial_refund" and claim["remedies"][0]["amount_cents"] == 1_20


def test_wrong_variant_fulfilled_is_an_order_mismatch(shop):
    client, store, fake = shop
    scen = prepared(client)
    who = session(client)
    *_, done = buy(client, fake, who, scen["shopper_prompt"])
    p = store.get("purchases", done["purchase"]["purchase_id"])
    fake.deliver_wrong_variant(p["shopify_order_id"], "gid://shopify/ProductVariant/51")
    claim = ok(client.post("/api/claims", json={"purchase_id": p["purchase_id"], "complaint_text": "This is the wrong size."}, headers=who))
    assert claim["claim"]["classification"] == "ORDER_MISMATCH"


def test_sessions_proxy_signature_and_admin_jwt(shop, monkeypatch):
    client, store, _ = shop
    # Without a session, Shopify mode never trusts a shopper id from the request body.
    assert client.post("/api/intent/parse", json={"shopper_id": "shp_jordan", "text": "a black hoodie"}).status_code == 401
    s = ok(client.post("/api/session/anonymous", json={}))
    again = ok(client.post("/api/session/anonymous", json={"previous_token": s["token"]}))
    assert again["shopper_id"] == s["shopper_id"] and s["shopper_id"].startswith("anon_")
    assert auth.read_session(s["token"][:-2] + "xx") is None

    params = {"shop": "acme.myshopify.com", "logged_in_customer_id": "777", "path_prefix": "/apps/perkpilot", "timestamp": str(int(time.time()))}
    msg = "".join(f"{k}={v}" for k, v in sorted(params.items()))
    sig = hmac.new(b"sec", msg.encode(), hashlib.sha256).hexdigest()
    signed = ok(client.get("/api/proxy/session", params={**params, "signature": sig}))
    assert signed["shopper_id"] == "cust_777" and signed["signed_in"]
    assert client.get("/api/proxy/session", params={**params, "logged_in_customer_id": "778", "signature": sig}).status_code == 401

    def jwt(claims, secret=b"sec"):
        enc = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()  # noqa: E731
        head, body = enc({"alg": "HS256", "typ": "JWT"}), enc(claims)
        return f"{head}.{body}." + base64.urlsafe_b64encode(hmac.new(secret, f"{head}.{body}".encode(), hashlib.sha256).digest()).rstrip(b"=").decode()

    good = {"iss": "https://acme.myshopify.com/admin", "dest": "https://acme.myshopify.com", "aud": "cid", "sub": "42",
            "exp": time.time() + 60, "nbf": time.time() - 5}
    assert client.get("/api/merchant/claims", headers={"Authorization": f"Bearer {jwt(good)}"}).status_code == 200
    for bad in [jwt(good, b"other"), jwt({**good, "aud": "x"}), jwt({**good, "exp": time.time() - 60}), jwt({**good, "dest": "https://evil.myshopify.com"})]:
        assert client.get("/api/merchant/claims", headers={"Authorization": f"Bearer {bad}"}).status_code == 401
    assert client.get("/api/admin/purchases", headers={"Authorization": f"Bearer {jwt(good)}"}).status_code == 200


def test_settings_change_shipping_and_card_offer(shop):
    client, store, _ = shop
    out = ok(client.post("/api/merchant/settings", headers=ADMIN, json={
        "shipping_options": [{"id": "standard", "label": "Ground (5 business days)", "price_cents": 5_00, "business_days": 5}],
        "card_offer": {"threshold_cents": 50_00}}))
    assert out["settings"]["card_offer"]["threshold_cents"] == 50_00 and out["settings"]["card_offer"]["credit_cents"] == 10_00
    assert store.get("products", "sfy_5")["delivery_options"][0]["label"] == "Ground (5 business days)"
    assert store.get("offers", "clo_visa")["minimum_spend_cents"] == 50_00
    bad = client.post("/api/merchant/settings", headers=ADMIN, json={"shipping_options": [{"id": "express", "label": "x", "price_cents": 1, "business_days": 1}]})
    assert bad.status_code == 422
