"""Seeded, fully synthetic demo data for the fictional store "StrideLab Running".

Every ID here is fixed so tests and demo replays are repeatable. No real card data,
customers or merchants are represented.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .core import DEMO_START, MERCHANT_ID, STORE_TZ, append_event, set_now
from .pricing import find_variant, price_cart
from .records import make_purchase_record, make_shipment, process_shipments
from .store import BaseStore

COLOR_HEX = {
    "black": "#1f2328",
    "white": "#f1f1ee",
    "grey": "#8a8f98",
    "navy": "#24324f",
    "blue": "#3d6fd9",
    "red": "#c8423a",
    "green": "#3f7d5a",
    "sand": "#cdb995",
    "orange": "#e07a2f",
}

STD = lambda price, days=3: {"id": "standard", "label": f"Standard ({days} business days)", "price_cents": price, "business_days": days}  # noqa: E731
EXP = lambda price, days=1: {"id": "express", "label": f"Express ({days} business day{'s' if days > 1 else ''})", "price_cents": price, "business_days": days}  # noqa: E731

SIZE_CHART = [
    {"us": "9", "eu": "42.5", "cm": "27.0"},
    {"us": "9.5", "eu": "43", "cm": "27.5"},
    {"us": "10", "eu": "44", "cm": "28.0"},
    {"us": "10.5", "eu": "44.5", "cm": "28.5"},
    {"us": "11", "eu": "45", "cm": "29.0"},
    {"us": "12", "eu": "46", "cm": "30.0"},
]


def _variants(prefix: str, spec: dict[str, dict[str, int]]) -> tuple[list[dict], dict[str, int]]:
    variants, inventory = [], {}
    for color, sizes in spec.items():
        for size, qty in sizes.items():
            vid = f"{prefix}-{color[:3]}-{size}"
            variants.append({"variant_id": vid, "color": color, "color_hex": COLOR_HEX[color], "size": size,
                             "sku": f"{prefix.upper()}-{color[:3].upper()}-{size.replace('.', '')}"})
            inventory[vid] = qty
    return variants, inventory


def _product(pid, name, brand, category, price, tax, description, spec, delivery, fit_note, accent, tags, rating):
    variants, inventory = _variants(pid, spec)
    return {
        "_id": pid,
        "product_id": pid,
        "merchant_id": MERCHANT_ID,
        "name": name,
        "brand": brand,
        "category": category,
        "description": description,
        "variants": variants,
        "available_variants": [v["variant_id"] for v in variants],
        "inventory_by_variant": inventory,
        "price_cents": price,
        "demo_tax_cents": tax,
        "currency": "USD",
        "delivery_options": delivery,
        "size_information": {"system": "US", "fit_note": fit_note, "chart": SIZE_CHART},
        "status": "active",
        "image": {"accent_hex": accent},
        "tags": tags,
        "rating": rating,
    }


def products() -> list[dict]:
    return [
        _product("aerox", "StrideLab AeroRunner X", "StrideLab", "running_shoes", 110_00, 7_00,
                 "Lightweight daily trainer with a responsive foam midsole and engineered mesh upper.",
                 {"black": {"9.5": 6, "10": 9, "10.5": 7, "11": 5}, "white": {"10": 4, "10.5": 3}},
                 [STD(8_00), EXP(20_00)], "Runs true to size per manufacturer chart.", "#e07a2f",
                 ["neutral", "daily trainer"], 4.6),
        _product("tempo", "StrideLab Tempo Lite", "StrideLab", "running_shoes", 95_00, 6_00,
                 "Low-profile tempo shoe for faster days. Ships from our partner warehouse.",
                 {"black": {"9.5": 3, "10": 5, "10.5": 4}, "blue": {"10": 2, "11": 3}},
                 [STD(8_00, 5), EXP(20_00, 2)], "Snug, performance fit.", "#3d6fd9", ["tempo", "lightweight"], 4.3),
        _product("cloud", "StrideLab Cloudline 2", "StrideLab", "running_shoes", 135_00, 8_50,
                 "Max-cushion long-run shoe with a wide, stable platform.",
                 {"black": {"10": 4, "10.5": 4, "11": 2}, "grey": {"9.5": 3, "10": 3}},
                 [STD(8_00), EXP(20_00)], "Roomy toe box.", "#8a8f98", ["max cushion"], 4.7),
        _product("pace", "StrideLab Pace Daily", "StrideLab", "running_shoes", 89_00, 5_75,
                 "Durable everyday runner. Popular sizes sell out quickly.",
                 {"black": {"9.5": 1, "10": 0, "10.5": 0}, "navy": {"10": 6, "10.5": 5}},
                 [STD(8_00), EXP(20_00)], "True to size.", "#24324f", ["value", "durable"], 4.2),
        _product("carbon", "StrideLab Marathon Elite Carbon", "StrideLab", "running_shoes", 210_00, 13_50,
                 "Carbon-plated race-day shoe.",
                 {"black": {"10": 3, "10.5": 2}, "orange": {"10": 2}},
                 [STD(0, 3), EXP(20_00)], "Race fit; consider half size up for long distances.", "#e07a2f",
                 ["race", "carbon plate"], 4.8),
        _product("ridge", "Ridgeback Trail GTX", "Ridgeback", "trail_running", 145_00, 9_25,
                 "Waterproof trail runner with aggressive lugs.",
                 {"green": {"10": 4, "10.5": 3, "11": 2}, "black": {"10": 2, "11": 2}},
                 [STD(8_00), EXP(20_00)], "True to size.", "#3f7d5a", ["waterproof", "trail"], 4.5),
        _product("walker", "Metro Walker", "Metro", "walking_shoes", 75_00, 4_80,
                 "All-day walking shoe with a cushioned heel.",
                 {"grey": {"9.5": 5, "10": 5, "11": 3}, "black": {"10": 4, "10.5": 4}},
                 [STD(6_00), EXP(18_00)], "Wide fit available.", "#8a8f98", ["comfort"], 4.1),
        _product("forge", "Forge Trainer", "Forge", "training_shoes", 100_00, 6_40,
                 "Stable gym trainer for lifting and HIIT.",
                 {"black": {"10": 5, "10.5": 5}, "red": {"10": 3}},
                 [STD(8_00), EXP(20_00)], "True to size.", "#c8423a", ["gym", "stable"], 4.4),
        _product("court", "Court Classic", "StrideLab", "casual_sneakers", 70_00, 4_50,
                 "Clean leather court sneaker.",
                 {"white": {"9.5": 6, "10": 6, "10.5": 6}, "black": {"10": 3}},
                 [STD(6_00), EXP(18_00)], "Runs slightly large.", "#cdb995", ["casual", "leather"], 4.0),
        _product("slide", "Recovery Slide", "StrideLab", "recovery_slides", 45_00, 2_90,
                 "Soft foam post-run recovery slide.",
                 {"black": {"10": 8, "11": 8}, "sand": {"10": 5}},
                 [STD(6_00), EXP(18_00)], "Whole sizes only; size down if between sizes.", "#cdb995", ["recovery"], 4.2),
    ]


def offers() -> list[dict]:
    return [
        {"_id": "cpn_stride15", "offer_id": "cpn_stride15", "merchant_id": MERCHANT_ID, "offer_type": "coupon",
         "code": "STRIDE15", "label": "STRIDE15 — $15 off running shoes", "amount_cents": 15_00, "percentage": None,
         "minimum_spend_cents": 100_00, "eligible_products": ["aerox", "cloud", "carbon"], "applicable_payment_methods": [],
         "stacking_group": "merchant_coupon", "expires_at": datetime(2026, 10, 31, 23, 59, tzinfo=STORE_TZ).isoformat(),
         "eligibility_price_basis": "merchandise_subtotal", "status": "active", "synthetic_fixture": True},
        {"_id": "cpn_welcome10", "offer_id": "cpn_welcome10", "merchant_id": MERCHANT_ID, "offer_type": "coupon",
         "code": "WELCOME10", "label": "WELCOME10 — $10 off any order over $50", "amount_cents": 10_00, "percentage": None,
         "minimum_spend_cents": 50_00, "eligible_products": [], "applicable_payment_methods": [],
         "stacking_group": "merchant_coupon", "expires_at": datetime(2026, 12, 31, 23, 59, tzinfo=STORE_TZ).isoformat(),
         "eligibility_price_basis": "merchandise_subtotal", "status": "active", "synthetic_fixture": True},
        {"_id": "cpn_spring20", "offer_id": "cpn_spring20", "merchant_id": MERCHANT_ID, "offer_type": "coupon",
         "code": "SPRING20", "label": "SPRING20 — $20 off (spring sale)", "amount_cents": 20_00, "percentage": None,
         "minimum_spend_cents": 80_00, "eligible_products": [], "applicable_payment_methods": [],
         "stacking_group": "merchant_coupon", "expires_at": datetime(2026, 5, 31, 23, 59, tzinfo=STORE_TZ).isoformat(),
         "eligibility_price_basis": "merchandise_subtotal", "status": "active", "synthetic_fixture": True},
        {"_id": "clo_visa10", "offer_id": "clo_visa10", "merchant_id": MERCHANT_ID,
         "offer_type": "card_linked_statement_credit", "code": None,
         "label": "Demo card-linked offer — $10 statement credit on $120+", "amount_cents": 10_00, "percentage": None,
         "minimum_spend_cents": 120_00, "eligible_products": [], "applicable_payment_methods": ["pm_visa_4242"],
         "stacking_group": "card_linked", "expires_at": datetime(2026, 11, 30, 23, 59, tzinfo=STORE_TZ).isoformat(),
         "eligibility_price_basis": "final_charge", "status": "active", "synthetic_fixture": True,
         "terms": "Synthetic test fixture. Credit posts to the statement after settlement when the actual card charge "
                  "(after discounts, including shipping and tax) is at least $120. It never reduces the checkout charge."},
    ]


def payment_methods() -> list[dict]:
    return [
        {"_id": "pm_visa_4242", "payment_method_id": "pm_visa_4242", "label": "Visa Demo Card •••• 4242",
         "network": "visa", "type": "credit", "last4": "4242", "enrolled_offers": ["clo_visa10"],
         "simulated": True, "note": "Test fixture, not a card credential or payment token."},
        {"_id": "pm_visa_1881", "payment_method_id": "pm_visa_1881", "label": "Visa Demo Debit •••• 1881",
         "network": "visa", "type": "debit", "last4": "1881", "enrolled_offers": [],
         "simulated": True, "note": "Test fixture, not a card credential or payment token."},
    ]


def shoppers() -> list[dict]:
    return [
        {"_id": "shp_jordan", "shopper_id": "shp_jordan", "display_name": "Jordan", "role": "Demo shopper #1",
         "payment_methods": ["pm_visa_4242", "pm_visa_1881"], "default_payment_method": "pm_visa_4242"},
        {"_id": "shp_sam", "shopper_id": "shp_sam", "display_name": "Sam", "role": "Demo shopper #2 (next customer)",
         "payment_methods": ["pm_visa_4242", "pm_visa_1881"], "default_payment_method": "pm_visa_4242"},
        {"_id": "shp_riley", "shopper_id": "shp_riley", "display_name": "Riley", "role": "Scenario shopper (edge cases)",
         "payment_methods": ["pm_visa_4242"], "default_payment_method": "pm_visa_4242"},
    ]


def policy() -> dict:
    return {
        "_id": "pol_stridelab", "merchant_id": MERCHANT_ID, "return_window_days": 30,
        "free_fit_exchange": True, "preference_return_fee_cents": 6_00,
        "keep_item_partial_refund_pct": 20, "allow_store_credit": True,
        "learning_thresholds": {"min_fulfilled_orders": 20, "min_validated_outcomes": 3,
                                "note": "Illustrative demo thresholds, not statistically validated."},
        "remedy_value_assumptions": {
            "future_contribution_margin_cents": 35_00,
            "repeat_purchase_probability": {"exchange": 0.55, "refund": 0.30, "partial_refund": 0.40,
                                             "keep_item_partial_refund": 0.45, "store_credit": 0.60,
                                             "manual_review": 0.25},
            "return_label_cost_cents": 6_00, "outbound_shipping_cost_cents": 8_00,
            "disclosure": "Demonstration assumptions only. Not a trained prediction. Never used to deny an eligible refund.",
        },
    }


# ---------------------------------------------------------------- historical orders


def _hist_purchase(store, *, pid, shopper_id, product, variant_id, placed: datetime, coupon=True,
                   delivered_variant_id=None, delivered_on=None, settle_cents=None, approved_override=None):
    variant = find_variant(product, variant_id)
    breakdown = price_cart(store, product, 1, "standard", ["STRIDE15"] if coupon and product["product_id"] == "aerox" else [],
                           [], "pm_visa_4242")
    if approved_override is not None:
        breakdown = {**breakdown, "charge_cents": approved_override}
    from .core import add_business_days

    expected = add_business_days(placed.date(), 3)
    rec = make_purchase_record(
        store, purchase_id=pid, shopper_id=shopper_id, product=product, variant=variant, quantity=1,
        breakdown=breakdown, payment_method_id="pm_visa_4242", approved_delivery_date=expected,
        verification_checks=[{"rule": "SEEDED_HISTORY", "status": "PASS", "explanation": "Seeded historical order."}],
        simulated_transaction_id=f"SIM-TXN-{pid.upper()}", created_at=placed, source="seed",
        authorization_event_id=f"seed-auth-{pid}",
    )
    store.put("purchases", rec)
    append_event(store, subject_type="purchase", subject_id=pid, purchase_id=pid, event_type="purchase.confirmed",
                 actor_type="shopper", payload={"approved_charge_cents": rec["approved_charge_cents"]}, source="seed", at=placed)
    append_event(store, subject_type="purchase", subject_id=pid, purchase_id=pid, event_type="payment.settled",
                 actor_type="payment_simulator",
                 payload={"amount_cents": settle_cents if settle_cents is not None else rec["approved_charge_cents"],
                          "simulated": True}, source="seed", at=placed + timedelta(hours=1))
    make_shipment(store, shipment_id=f"shp_{pid}", purchase_id=pid, variant_id=variant_id, placed_at=placed,
                  expected=expected, delivered_variant_id=delivered_variant_id, delivered_on=delivered_on)
    return rec


def _hist_fit_claim(store, *, claim_id, purchase, at: datetime, validated: bool, to_size="10.5"):
    exchange_variant = f"aerox-bla-{to_size}"
    claim = {
        "_id": claim_id, "claim_id": claim_id, "purchase_id": purchase["purchase_id"], "shopper_id": purchase["shopper_id"],
        "merchant_id": MERCHANT_ID, "complaint_text": "The size 10 feels too tight across the toes.",
        "structured_issue": {"problem_type": "fit_tight", "details": "tight across toes", "requested_outcome": "exchange",
                             "mentioned_size": None, "mentioned_color": None, "missing_information": []},
        "interpreter": {"mode": "seed", "note": "Seeded historical claim."},
        "classification": "SUSPECTED_PRODUCT_FIT", "classification_reason": "Delivered size matched order; shopper reported tight fit.",
        "evidence_references": [], "evidence_status": "confirmed_outcome" if validated else "customer_report_only",
        "requires_review": False, "responsibility": "merchant_goodwill_policy",
        "status": "resolved" if validated else "closed_refunded",
        "accepted_remedy_id": None,
        "validated_outcome": ({"type": "fit_exchange_confirmed", "product_id": "aerox", "from_size": "10",
                               "to_size": to_size, "replacement_variant_id": exchange_variant,
                               "confirmed_at": (at + timedelta(days=6)).isoformat()} if validated else None),
        "created_at": at.isoformat(), "source": "seed", "follow_up_question": None, "history": [],
    }
    store.put("claims", claim)
    append_event(store, subject_type="claim", subject_id=claim_id, purchase_id=purchase["purchase_id"],
                 event_type="claim.created", actor_type="shopper", payload={"classification": claim["classification"]},
                 source="seed", at=at)
    if validated:
        append_event(store, subject_type="claim", subject_id=claim_id, purchase_id=purchase["purchase_id"],
                     event_type="remedy.executed", actor_type="system",
                     payload={"remedy_type": "exchange", "to_size": to_size, "simulated": True}, source="seed",
                     at=at + timedelta(days=1))
        append_event(store, subject_type="claim", subject_id=claim_id, purchase_id=purchase["purchase_id"],
                     event_type="claim.outcome_confirmed", actor_type="shopper",
                     payload={"outcome": "fits", "to_size": to_size}, source="seed", at=at + timedelta(days=6))
        p = store.get("purchases", purchase["purchase_id"])
        p["status"] = "exchanged"
        store.put("purchases", p)
    else:
        append_event(store, subject_type="claim", subject_id=claim_id, purchase_id=purchase["purchase_id"],
                     event_type="remedy.executed", actor_type="system",
                     payload={"remedy_type": "refund", "simulated": True,
                              "note": "Refunded without exchange; fit never confirmed, so not a validated outcome."},
                     source="seed", at=at + timedelta(days=1))
        p = store.get("purchases", purchase["purchase_id"])
        p["status"] = "refunded"
        store.put("purchases", p)


def seed(store: BaseStore) -> None:
    store.clear_all()
    # Start the clock in early September so seeded shipments deliver in the past.
    set_now(store, datetime(2026, 9, 1, 9, 0, tzinfo=STORE_TZ))
    store.put("merchants", {"_id": MERCHANT_ID, "merchant_id": MERCHANT_ID, "name": "StrideLab Running",
                            "tagline": "Fictional demo store", "timezone": "America/Los_Angeles"})
    store.put_many("products", products())
    store.put_many("offers", offers())
    store.put_many("payment_methods", payment_methods())
    store.put_many("shoppers", shoppers())
    store.put("policies", policy())
    prods = {p["product_id"]: p for p in store.find("products")}
    aerox = prods["aerox"]

    # 20 fulfilled size-10 AeroRunner X orders (the learning comparison group).
    hist = []
    for i in range(1, 21):
        placed = datetime.combine(date(2026, 9, 1) + timedelta(days=i), time(10, 0), tzinfo=STORE_TZ)
        hist.append(_hist_purchase(store, pid=f"hist_{i:02d}", shopper_id=f"hist_shopper_{i:02d}", product=aerox,
                                   variant_id="aerox-bla-10", placed=placed))
    # A few orders for other sizes/products so dashboards aren't a single bar.
    extra = [("hist_21", "aerox", "aerox-bla-10.5"), ("hist_22", "aerox", "aerox-bla-11"), ("hist_23", "aerox", "aerox-bla-9.5"),
             ("hist_24", "cloud", "cloud-bla-10"), ("hist_25", "tempo", "tempo-bla-10"), ("hist_26", "ridge", "ridge-gre-10.5")]
    for j, (pid, prod_id, vid) in enumerate(extra):
        placed = datetime.combine(date(2026, 9, 3) + timedelta(days=j * 2), time(11, 0), tzinfo=STORE_TZ)
        _hist_purchase(store, pid=pid, shopper_id=f"hist_shopper_{21 + j:02d}", product=prods[prod_id], variant_id=vid, placed=placed)

    set_now(store, datetime(2026, 10, 1, 9, 0, tzinfo=STORE_TZ))
    process_shipments(store, source="seed")

    # Two validated size-10 fit outcomes (exchanged to 10.5, replacement confirmed to fit)
    # and one unvalidated fit report that ended in a refund (not counted as evidence).
    _hist_fit_claim(store, claim_id="clm_hist_01", purchase=hist[3], at=datetime(2026, 9, 12, 15, 0, tzinfo=STORE_TZ), validated=True)
    _hist_fit_claim(store, claim_id="clm_hist_02", purchase=hist[10], at=datetime(2026, 9, 19, 15, 0, tzinfo=STORE_TZ), validated=True)
    _hist_fit_claim(store, claim_id="clm_hist_03", purchase=hist[15], at=datetime(2026, 9, 24, 15, 0, tzinfo=STORE_TZ), validated=False)

    # Edge-case purchases for shopper Riley (resolution scenarios).
    def riley(pid, vid, placed_day, **kw):
        placed = datetime.combine(date(2026, 10, placed_day), time(10, 0), tzinfo=STORE_TZ)
        return _hist_purchase(store, pid=pid, shopper_id="shp_riley", product=aerox, variant_id=vid, placed=placed, **kw)

    riley("sc_wrong_color", "aerox-bla-10.5", 12, delivered_variant_id="aerox-whi-10.5")
    riley("sc_incorrect_charge", "aerox-bla-11", 12, settle_cents=125_00)
    riley("sc_late_delivery", "aerox-bla-9.5", 12, delivered_on=date(2026, 10, 19))
    riley("sc_preference", "aerox-bla-10.5", 13)
    riley("sc_ambiguous", "aerox-bla-11", 13)

    set_now(store, DEMO_START)
    process_shipments(store, source="seed")
    store.put("meta", {"_id": "seed_info", "seeded_at_demo_time": DEMO_START.isoformat(), "version": "demo-v1"})
