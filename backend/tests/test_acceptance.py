"""Specification section 18: required engineering tests, exercised through the HTTP API."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.store import FileStore

from .conftest import REQUEST, failed_rules, ok, proposal


def verify(client, intent_id, **kw):
    return ok(client.post("/api/checkout/verify", json={"intent_id": intent_id, "proposal": proposal(**kw)}))


def buy(client, confirmed_intent, shopper="shp_jordan"):
    intent = confirmed_intent(shopper)
    q = verify(client, intent["intent_id"])
    assert q["result"]["status"] == "PASS"
    res = ok(client.post("/api/checkout/confirm", json={"quote_id": q["quote_id"], "shopper_id": shopper,
                                                         "approved_charge_cents": q["result"]["price_breakdown"]["charge_cents"],
                                                         "shopper_approved": True}))
    return res["purchase"]


def deliver(client, purchase_id):
    ok(client.post("/api/demo/advance", json={"until": "delivered", "purchase_id": purchase_id}))


# -------------------------------------------------------------------------- 1-8 checkout


def test_01_correct_product_passes(client, confirmed_intent):
    q = verify(client, confirmed_intent()["intent_id"])
    assert q["result"]["status"] == "PASS"
    bd = q["result"]["price_breakdown"]
    assert (bd["merchandise_cents"], bd["shipping_cents"], bd["tax_cents"], bd["coupon_discount_cents"], bd["charge_cents"]) == (
        11000, 800, 700, 1500, 11000)


def test_02_wrong_variant_blocked_with_explanation(client, confirmed_intent):
    iid = confirmed_intent()["intent_id"]
    q = verify(client, iid, variant_id="aerox-whi-10")
    assert q["result"]["status"] == "FAIL" and "COLOR" in failed_rules(q)
    color = next(c for c in q["result"]["checks"] if c["rule"] == "COLOR")
    assert color["observed"] == "white" and color["required"] == "black" and color["explanation"]
    q = verify(client, iid, variant_id="aerox-bla-10.5")
    assert "SIZE" in failed_rules(q)
    # a corrected option is offered and it passes
    passing = [o for o in q["result"]["options"] if o["status"] == "PASS"]
    assert passing and passing[0]["proposal"]["variant_id"] == "aerox-bla-10"


def test_03_budget_violation_blocked(client, confirmed_intent):
    q = verify(client, confirmed_intent()["intent_id"], coupon_codes=[])
    assert q["result"]["status"] == "FAIL" and "BUDGET" in failed_rules(q)
    assert q["result"]["price_breakdown"]["charge_cents"] == 12500


def test_04_coupon_invalidates_card_offer_threshold(client, confirmed_intent):
    """The canonical agent mistake: $15 coupon + $10 card credit claimed on a $110 charge."""
    iid = confirmed_intent()["intent_id"]
    rec = ok(client.post("/api/recommendations", json={"intent_id": iid}))
    agent = rec["agent_proposal"]
    assert agent["card_offer_ids"] == ["clo_visa10"] and agent["claimed_effective_cost_cents"] == 10000
    q = ok(client.post("/api/checkout/verify", json={"intent_id": iid, "proposal": agent}))
    assert q["result"]["status"] == "FAIL"
    card = next(c for c in q["result"]["checks"] if c["rule"] == "CARD_OFFER_ELIGIBILITY")
    assert card["status"] == "FAIL" and "$110.00" in card["observed"]
    opts = {o["option_id"]: o for o in q["result"]["options"]}
    a, b = opts["A"], opts["B"]
    assert a["status"] == "PASS" and a["charge_cents"] == 11000 and a["effective_cost_cents"] == 11000 and not a["card_offer"]["eligible"]
    assert b["coupon"] is None and b["charge_cents"] == 12500 and b["later_statement_credit_cents"] == 1000
    assert b["effective_cost_cents"] == 11500 and not b["within_budget"] and b["status"] == "FAIL"
    assert q["result"]["recommended_option"] == "A"


def test_05_expired_coupon_rejected(client, confirmed_intent):
    q = verify(client, confirmed_intent()["intent_id"], coupon_codes=["SPRING20"])
    assert "COUPON_EXPIRED" in failed_rules(q)


def test_05b_coupons_in_same_group_do_not_stack(client, confirmed_intent):
    q = verify(client, confirmed_intent()["intent_id"], coupon_codes=["STRIDE15", "WELCOME10"])
    assert "OFFER_STACKING" in failed_rules(q)


def test_06_missed_delivery_requirement(client, confirmed_intent):
    q = verify(client, confirmed_intent()["intent_id"], product_id="tempo", variant_id="tempo-bla-10", coupon_codes=[])
    assert q["result"]["status"] == "FAIL" and "DELIVERY_DEADLINE" in failed_rules(q)


def test_07_requires_shopper_approval_and_current_version(client, confirmed_intent):
    intent = confirmed_intent()
    q = verify(client, intent["intent_id"])
    body = {"quote_id": q["quote_id"], "shopper_id": "shp_jordan", "approved_charge_cents": 11000, "shopper_approved": False}
    r = client.post("/api/checkout/confirm", json=body)
    assert r.status_code == 400 and r.json()["error"]["code"] == "approval_required"
    # editing requirements after the quote invalidates it
    ok(client.post("/api/intent/confirm", json={"intent_id": intent["intent_id"],
                                                "confirmed_requirements": {**intent["confirmed_requirements"], "max_charge_cents": 11500}}))
    r = client.post("/api/checkout/confirm", json={**body, "shopper_approved": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NEEDS_RECONFIRMATION"
    # a failed quote can never be confirmed
    bad = verify(client, intent["intent_id"], coupon_codes=[])
    r = client.post("/api/checkout/confirm", json={**body, "quote_id": bad["quote_id"], "shopper_approved": True})
    assert r.status_code == 409


def test_08_changed_amount_after_approval(client, confirmed_intent):
    q = verify(client, confirmed_intent()["intent_id"])
    ok(client.post("/api/merchant/products/aerox/price", json={"price_cents": 11400}))
    r = client.post("/api/checkout/confirm", json={"quote_id": q["quote_id"], "shopper_id": "shp_jordan",
                                                    "approved_charge_cents": 11000, "shopper_approved": True})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "NEEDS_RECONFIRMATION" and err["detail"]["new_quote"]["result"]["price_breakdown"]["charge_cents"] == 11400
    # the stale quote can't be reused, and no purchase exists
    assert ok(client.get("/api/purchases", params={"shopper_id": "shp_jordan"}))["purchases"] == []


def test_frontend_totals_are_ignored(client, confirmed_intent):
    q = verify(client, confirmed_intent()["intent_id"], claimed_charge_cents=9000)
    assert "CLAIMED_CHARGE" in failed_rules(q)
    assert q["result"]["price_breakdown"]["charge_cents"] == 11000


# -------------------------------------------------------------------------- 9-12 resolution


def test_09_wrong_delivery_classified_from_fulfillment_evidence(client):
    v = ok(client.post("/api/claims", json={"purchase_id": "sc_wrong_color", "shopper_id": "shp_riley",
                                            "complaint_text": "These are the wrong color."}))
    assert v["claim"]["classification"] == "ORDER_MISMATCH"
    assert any(e["type"] == "fulfillment" and "White" in e["summary"] for e in v["claim"]["evidence_references"])
    types = {r["remedy_type"] for r in v["remedies"] if r["status"] == "eligible"}
    assert {"exchange", "refund", "keep_item_partial_refund"} <= types


def test_09b_charge_and_delivery_mismatches(client):
    v = ok(client.post("/api/claims", json={"purchase_id": "sc_incorrect_charge", "shopper_id": "shp_riley",
                                            "complaint_text": "I was charged more than I approved"}))
    assert v["claim"]["classification"] == "PRICE_OR_DELIVERY_MISMATCH" and v["claim"]["overcharge_cents"] == 1500
    v = ok(client.post("/api/claims", json={"purchase_id": "sc_late_delivery", "shopper_id": "shp_riley",
                                            "complaint_text": "It arrived late, after the promised date"}))
    assert v["claim"]["classification"] == "PRICE_OR_DELIVERY_MISMATCH" and v["claim"]["mismatch_kind"] == "delivery"


def test_10_fit_complaint_is_not_merchant_fault(client, confirmed_intent):
    p = buy(client, confirmed_intent)
    deliver(client, p["purchase_id"])
    v = ok(client.post("/api/claims", json={"purchase_id": p["purchase_id"], "shopper_id": "shp_jordan",
                                            "complaint_text": "These shoes feel too tight."}))
    c = v["claim"]
    assert c["classification"] == "SUSPECTED_PRODUCT_FIT"
    assert c["responsibility"] != "merchant_fulfillment_error" and "not a proven product defect" in c["classification_reason"]
    assert c["prior_validated_outcomes"] == 2


def test_10b_ambiguous_complaint_asks_follow_up(client):
    v = ok(client.post("/api/claims", json={"purchase_id": "sc_ambiguous", "shopper_id": "shp_riley",
                                            "complaint_text": "This isn't what I expected."}))
    assert v["claim"]["classification"] == "NEEDS_REVIEW" and v["claim"]["follow_up_question"]
    assert {r["remedy_type"] for r in v["remedies"]} == {"manual_review"}
    v = ok(client.post(f"/api/claims/{v['claim']['claim_id']}/clarify", json={"text": "I just prefer a different look, I changed my mind"}))
    assert v["claim"]["classification"] == "CUSTOMER_PREFERENCE"
    assert any(r["remedy_type"] == "refund" for r in v["remedies"])


def test_11_exchange_is_verified_and_inventory_reserved(client, store, confirmed_intent):
    p = buy(client, confirmed_intent)
    deliver(client, p["purchase_id"])
    v = ok(client.post("/api/claims", json={"purchase_id": p["purchase_id"], "shopper_id": "shp_jordan",
                                            "complaint_text": "too tight"}))
    ex = next(r for r in v["remedies"] if r["remedy_type"] == "exchange")
    rules = {c["rule"] for c in ex["exchange_verification"]["checks"]}
    assert {"STOCK", "PRICE_NEUTRAL", "SAME_PRODUCT"} <= rules and ex["target_variant_id"] == "aerox-bla-10.5"
    before = store.get("products", "aerox")["inventory_by_variant"]["aerox-bla-10.5"]
    ok(client.post(f"/api/remedies/{ex['remedy_id']}/accept", json={"shopper_id": "shp_jordan", "confirm": True}))
    assert store.get("products", "aerox")["inventory_by_variant"]["aerox-bla-10.5"] == before - 1


def test_11b_exchange_blocked_when_sold_out(client, store, confirmed_intent):
    p = buy(client, confirmed_intent)
    deliver(client, p["purchase_id"])
    v = ok(client.post("/api/claims", json={"purchase_id": p["purchase_id"], "shopper_id": "shp_jordan", "complaint_text": "too tight"}))
    ex = next(r for r in v["remedies"] if r["remedy_type"] == "exchange")
    prod = store.get("products", "aerox")
    prod["inventory_by_variant"]["aerox-bla-10.5"] = 0
    store.put("products", prod)
    r = client.post(f"/api/remedies/{ex['remedy_id']}/accept", json={"shopper_id": "shp_jordan", "confirm": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "inventory_conflict"


def test_12_duplicate_remedy_is_idempotent(client, store):
    v = ok(client.post("/api/claims", json={"purchase_id": "sc_incorrect_charge", "shopper_id": "shp_riley",
                                            "complaint_text": "overcharged"}))
    rid = next(r for r in v["remedies"] if r["remedy_type"] == "partial_refund")["remedy_id"]
    body = {"shopper_id": "shp_riley", "confirm": True, "idempotency_key": "abc"}
    first = ok(client.post(f"/api/remedies/{rid}/accept", json=body))
    second = ok(client.post(f"/api/remedies/{rid}/accept", json=body))
    assert not first["duplicate"] and second["duplicate"]
    assert len(store.find("events", {"event_type": "refund.simulated", "subject_id": "sc_incorrect_charge"})) == 1
    other = next(r for r in v["remedies"] if r["remedy_id"] != rid)["remedy_id"]
    assert client.post(f"/api/remedies/{other}/accept", json=body).status_code == 409


# -------------------------------------------------------------------------- 13-14 learning


def _full_loop(client, confirmed_intent):
    p = buy(client, confirmed_intent)
    deliver(client, p["purchase_id"])
    v = ok(client.post("/api/claims", json={"purchase_id": p["purchase_id"], "shopper_id": "shp_jordan",
                                            "complaint_text": "These shoes feel too tight."}))
    ex = next(r for r in v["remedies"] if r["remedy_type"] == "exchange")
    v = ok(client.post(f"/api/remedies/{ex['remedy_id']}/accept", json={"shopper_id": "shp_jordan", "confirm": True}))
    r = client.post(f"/api/claims/{v['claim']['claim_id']}/outcome", json={"shopper_id": "shp_jordan", "outcome": "fits"})
    assert r.status_code == 409  # replacement not delivered yet
    deliver(client, v["exchange"]["exchange_purchase_id"])
    out = ok(client.post(f"/api/claims/{v['claim']['claim_id']}/outcome", json={"shopper_id": "shp_jordan", "outcome": "fits"}))
    return p, out


def test_13_rule_inactive_until_approved(client, confirmed_intent):
    _, out = _full_loop(client, confirmed_intent)
    rule = out["rule_proposal"]
    assert rule["status"] == "proposed" and rule["confirmed_outcome_count"] == 3 and rule["affected_order_count"] == 21
    assert any("Small sample" in lim for lim in rule["limitations"])
    sam = confirmed_intent("shp_sam")
    assert ok(client.post("/api/recommendations", json={"intent_id": sam["intent_id"]}))["guidance"] == []
    q = verify(client, sam["intent_id"])
    assert not any(c["rule"] == "LEARNED_FIT_GUIDANCE" for c in q["result"]["checks"])


def test_14_learning_loop_changes_next_recommendation(client, confirmed_intent, admin):
    p, out = _full_loop(client, confirmed_intent)
    ok(client.post(f"/api/merchant/rules/{out['rule_proposal']['rule_id']}/approve", json={"actor": "merchant_admin"}))
    sam = confirmed_intent("shp_sam")
    rec = ok(client.post("/api/recommendations", json={"intent_id": sam["intent_id"]}))
    assert rec["guidance"] and rec["guidance"][0]["suggest_size"] == "10.5"
    assert "size 10.5" in rec["guidance"][0]["message"]
    base = ok(client.post("/api/recommendations", json={"intent_id": sam["intent_id"], "apply_learned_rules": False}))
    assert base["guidance"] == []
    # guidance is advisory: the shopper's size-10 cart still passes, nothing changed silently
    q = verify(client, sam["intent_id"])
    assert q["result"]["status"] == "PASS" and q["proposal"]["variant_id"] == "aerox-bla-10"
    assert any(c["rule"] == "LEARNED_FIT_GUIDANCE" and not c["blocking"] for c in q["result"]["checks"])
    # the earlier customer's confirmed order is untouched and its record still verifies
    rec1 = ok(client.get(f"/api/admin/purchases/{p['purchase_id']}/audit", headers=admin))
    assert rec1["purchase"]["variant_id"] == "aerox-bla-10" and rec1["integrity"]["hash_matches"]
    # pausing the rule removes the guidance again
    ok(client.post(f"/api/merchant/rules/{out['rule_proposal']['rule_id']}/toggle", json={"enabled": False}))
    assert ok(client.post("/api/recommendations", json={"intent_id": sam["intent_id"]}))["guidance"] == []


# -------------------------------------------------------------------------- 15-16 persistence


def test_15_state_persists_across_restarts(tmp_path: Path, admin):
    path = tmp_path / "state.json"
    c1 = TestClient(create_app(FileStore(path)))
    it = ok(c1.post("/api/intent/parse", json={"shopper_id": "shp_jordan", "text": REQUEST}))
    ok(c1.post("/api/intent/confirm", json={"intent_id": it["intent_id"], "confirmed_requirements": it["parsed_requirements"]}))
    q = ok(c1.post("/api/checkout/verify", json={"intent_id": it["intent_id"], "proposal": proposal()}))
    pur = ok(c1.post("/api/checkout/confirm", json={"quote_id": q["quote_id"], "shopper_id": "shp_jordan",
                                                     "approved_charge_cents": 11000, "shopper_approved": True}))["purchase"]
    before = ok(c1.get(f"/api/admin/purchases/{pur['purchase_id']}/audit", headers=admin))
    c2 = TestClient(create_app(FileStore(path)))  # simulates a backend restart / page refresh
    got = ok(c2.get(f"/api/admin/purchases/{pur['purchase_id']}/audit", headers=admin))
    assert got["purchase"]["record_hash"] == before["purchase"]["record_hash"] and got["integrity"]["event_chain_valid"]
    assert ok(c2.get(f"/api/purchases/{pur['purchase_id']}", params={"shopper_id": "shp_jordan"}))["purchase"]["verification"]["verified"]


def test_16_demo_reset_restores_seed(client, store, confirmed_intent):
    buy(client, confirmed_intent)
    assert ok(client.get("/api/purchases", params={"shopper_id": "shp_jordan"}))["purchases"]
    ok(client.post("/api/demo/reset"))
    assert ok(client.get("/api/purchases", params={"shopper_id": "shp_jordan"}))["purchases"] == []
    assert store.get("products", "aerox")["inventory_by_variant"]["aerox-bla-10"] == 9
    assert ok(client.get("/api/demo/state"))["now"].startswith("2026-10-20T10:00")
    buy(client, confirmed_intent)  # and the demo replays


# -------------------------------------------------------------------------- extras


def test_record_hash_detects_tampering(client, store, confirmed_intent, admin):
    p = buy(client, confirmed_intent)
    doc = store.get("purchases", p["purchase_id"])
    doc["approved_charge_cents"] = 9900
    store.put("purchases", doc)
    assert not ok(client.get(f"/api/admin/purchases/{p['purchase_id']}/audit", headers=admin))["integrity"]["hash_matches"]
    # the shopper isn't shown the hash, but no longer sees the order as verified
    mine = ok(client.get(f"/api/purchases/{p['purchase_id']}", params={"shopper_id": "shp_jordan"}))["purchase"]
    assert mine["verification"]["verified"] is False


INTERNAL = {"record_hash", "verification_results", "authorization_event_id", "simulated_transaction_id", "intent_id",
            "confirmation_version", "full_price_breakdown", "payment_method_reference", "shopper_id"}


def test_customer_views_hide_audit_data(client, confirmed_intent):
    """Shoppers get order/payment/shipping status and one verified flag; hashes, checks and the event log never leave the API."""
    p = buy(client, confirmed_intent)
    assert not INTERNAL & set(p) and p["verification"] == {"verified": True, "checks_passed": p["verification"]["checks_total"],
                                                          "checks_total": p["verification"]["checks_total"]}
    assert p["verification"]["checks_total"] >= 9 and p["payment"] == {"status": "Paid", "method": "Visa Demo Card •••• 4242", "simulated": True}
    got = ok(client.get(f"/api/purchases/{p['purchase_id']}", params={"shopper_id": "shp_jordan"}))
    assert set(got) == {"purchase", "claims", "exchange_orders", "product"} and not INTERNAL & set(got["purchase"])
    assert got["purchase"]["shipping"]["simulated"] and got["purchase"]["shipping"]["steps"][0]["status"] == "label_created"
    [listed] = ok(client.get("/api/purchases", params={"shopper_id": "shp_jordan"}))["purchases"]
    assert not INTERNAL & set(listed)
    assert client.get(f"/api/purchases/{p['purchase_id']}").status_code == 422  # shopper required
    deliver(client, p["purchase_id"])
    v = ok(client.post("/api/claims", json={"purchase_id": p["purchase_id"], "shopper_id": "shp_jordan", "complaint_text": "too tight"}))
    assert "events" not in v and v["timeline"][0]["label"] == "Issue reported" and not INTERNAL & set(v["purchase"])
    assert "hash" not in str(v["claim"]["evidence_references"]).lower()
    q = verify(client, confirmed_intent()["intent_id"])
    assert "quote_hash" not in q and "quote_hash" not in ok(client.get(f"/api/checkout/quotes/{q['quote_id']}"))["quote"]


def test_audit_routes_need_admin_token_in_every_mode(client, confirmed_intent, monkeypatch):
    p = buy(client, confirmed_intent)
    url = f"/api/admin/purchases/{p['purchase_id']}/audit"
    monkeypatch.setenv("PERKPILOT_ADMIN_TOKEN", "")
    assert client.get(url).json()["error"]["code"] == "admin_not_configured"
    monkeypatch.setenv("PERKPILOT_ADMIN_TOKEN", "t0ken")
    for headers in ({}, {"Authorization": "Bearer nope"}):
        assert client.get(url, headers=headers).status_code == 401
        assert client.get("/api/admin/purchases", headers=headers).status_code == 401
    audit = ok(client.get(url, headers={"Authorization": "Bearer t0ken"}))
    assert audit["integrity"]["hash_matches"] and audit["events"] and len(audit["verification_results"]) >= 9
    assert ok(client.get("/api/admin/purchases", headers={"Authorization": "Bearer t0ken"}))["purchases"][0]["verified"]


def test_live_mode_hides_simulated_orders(store, client, confirmed_intent, monkeypatch):
    p = buy(client, confirmed_intent)  # a simulated demo purchase in the shared store
    monkeypatch.setenv("PERKPILOT_MODE", "live")
    monkeypatch.setenv("PERKPILOT_ADMIN_TOKEN", "t0ken")
    live = TestClient(create_app(store))
    assert ok(live.get("/api/purchases", params={"shopper_id": "shp_jordan"}))["purchases"] == []
    assert live.get(f"/api/purchases/{p['purchase_id']}", params={"shopper_id": "shp_jordan"}).status_code == 404


def test_shopper_isolation(client, confirmed_intent):
    p = buy(client, confirmed_intent)
    r = client.post("/api/claims", json={"purchase_id": p["purchase_id"], "shopper_id": "shp_sam", "complaint_text": "too tight"})
    assert r.status_code == 403
    assert client.get(f"/api/purchases/{p['purchase_id']}", params={"shopper_id": "shp_sam"}).status_code == 404


def test_parse_flags_missing_essentials(client):
    it = ok(client.post("/api/intent/parse", json={"shopper_id": "shp_jordan", "text": "I want some shoes"}))
    assert set(it["missing_essential"]) == {"category", "size", "max_charge_cents"}
    r = client.post("/api/intent/confirm", json={"intent_id": it["intent_id"], "confirmed_requirements": it["parsed_requirements"]})
    assert r.status_code == 422


def test_evaluation_run_is_sandboxed(client, store):
    run = ok(client.post("/api/eval/run"))
    m = run["calculated_metrics"]
    assert m["violation_detection"] == "10/10" and m["false_rejections"] == "0/5" and m["workflow_completion"] == "12/12"
    assert m["comparison_intervention"] == "5/5"
    assert store.find("rules") == []  # live state untouched


def test_unavailable_brand_explains_and_offers_a_way_forward(client, confirmed_intent):
    it = ok(client.post("/api/intent/parse", json={"shopper_id": "shp_jordan", "text": "Find Nike running shoes size 10 under $150 total"}))
    assert any("doesn't carry" in n for n in it["notes"])
    intent = confirmed_intent(text="Find Nike running shoes size 10 under $150 total", brand="Nike")
    rec = ok(client.post("/api/recommendations", json={"intent_id": intent["intent_id"]}))
    assert rec["top_pick"] is None and any("doesn't carry Nike" in n for n in rec["notices"])
    assert rec["candidates"] and all("Brand is" in c["reasons"][0] for c in rec["candidates"])  # no silent empty list
    relax = rec["relaxations"][0]
    assert relax["fields"] == ["brand"] and relax["count"] >= 1
    # the shopper accepts the relaxation (a new confirmed version), and results appear
    ok(client.post("/api/intent/confirm", json={"intent_id": intent["intent_id"], "change_reason": "widened search",
                                                "confirmed_requirements": {**intent["confirmed_requirements"], "brand": None}}))
    assert ok(client.post("/api/recommendations", json={"intent_id": intent["intent_id"]}))["top_pick"]
