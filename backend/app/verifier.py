"""Deterministic Checkout Verifier.

The verifier is the only component allowed to decide whether a proposed cart may reach
checkout. It recomputes every amount server-side from catalog and offer fixtures and
never trusts totals supplied by the frontend or an AI agent.
"""

from __future__ import annotations

from datetime import date
from itertools import product as cartesian

from .core import fmt_cents, now
from .pricing import estimate_delivery, find_shipping, find_variant, offer_by_code, price_cart, size_label, unit_price
from .store import BaseStore

PASS, FAIL, WARN, INFO = "PASS", "FAIL", "WARN", "INFO"


def _check(rule, label, status, observed=None, required=None, explanation="", blocking=None):
    return {
        "rule": rule,
        "label": label,
        "status": status,
        "blocking": (status == FAIL) if blocking is None else blocking,
        "observed": observed,
        "required": required,
        "explanation": explanation,
    }


def _day(d: date) -> str:
    """'Mon Oct 6' without the glibc-only %-d (crashes on Windows)."""
    return f"{d:%a %b} {d.day}"


def active_rules_for(store: BaseStore, product_id: str, size: str) -> list[dict]:
    return [r for r in store.find("rules", {"product_scope": product_id, "status": "approved"})
            if r.get("enabled", True) and r["variant_scope"].get("size") == size]


def matching_variant(product: dict, req: dict) -> dict | None:
    for v in product["variants"]:
        if req.get("color") and v["color"] != req["color"]:
            continue
        if req.get("size") and v["size"] != req["size"]:
            continue
        return v
    return None


def verify(store: BaseStore, req: dict, proposal: dict, shopper_id: str, *, apply_learned_rules: bool = True,
           with_options: bool = True) -> dict:
    """Run every checkout rule against the shopper-confirmed requirements.

    Returns PASS, FAIL, or NEEDS_RECONFIRMATION plus the itemised checks, the server-side
    price breakdown, and (on failure) corrected options that are themselves re-verified.
    """
    checks: list[dict] = []
    product = store.get("products", proposal["product_id"])
    if not product or product["status"] != "active":
        checks.append(_check("PRODUCT_EXISTS", "Product available", FAIL, proposal["product_id"], "active catalog product",
                             "This product is not in the merchant's active catalog."))
        return {"status": FAIL, "checks": checks, "price_breakdown": None, "options": [], "recommended_option": None,
                "guidance": [], "estimated_delivery_date": None}

    variant = find_variant(product, proposal["variant_id"])
    qty = proposal.get("quantity", 1)

    # ---- product identity
    if req.get("category"):
        ok = product["category"] == req["category"]
        checks.append(_check("PRODUCT_CATEGORY", "Product type", PASS if ok else FAIL, product["category"], req["category"],
                             "Matches the requested product type." if ok else "This product is a different type than you asked for."))
    if req.get("brand"):
        ok = product["brand"].lower() == req["brand"].lower()
        checks.append(_check("BRAND", "Brand", PASS if ok else FAIL, product["brand"], req["brand"],
                             "Matches your brand preference." if ok else "Different brand than you asked for."))
    if not variant:
        checks.append(_check("VARIANT_EXISTS", "Variant", FAIL, proposal["variant_id"], "a valid variant of this product",
                             "That size/color combination does not exist for this product."))
    else:
        if req.get("color"):
            ok = variant["color"] == req["color"]
            checks.append(_check("COLOR", "Color", PASS if ok else FAIL, variant["color"], req["color"],
                                 "Matches your color." if ok else f"Proposed {variant['color']}, but you confirmed {req['color']}."))
        if req.get("size"):
            ok = variant["size"] == req["size"]
            checks.append(_check("SIZE", "Size", PASS if ok else FAIL, size_label(variant["size"]), size_label(req["size"]),
                                 "Matches your confirmed size." if ok else f"Proposed size {variant['size']}, but you confirmed size {req['size']}."))
        stock = product["inventory_by_variant"].get(variant["variant_id"], 0)
        ok = stock >= qty
        checks.append(_check("STOCK", "In stock", PASS if ok else FAIL, f"{stock} available", f"{qty} needed",
                             "Inventory is available." if ok else "Not enough inventory for this variant."))
    ok = qty == req.get("quantity", 1)
    checks.append(_check("QUANTITY", "Quantity", PASS if ok else FAIL, qty, req.get("quantity", 1),
                         "Matches the quantity you confirmed." if ok else "Quantity differs from what you confirmed."))

    if not product["delivery_options"]:
        # Platform-synced products (e.g. Shopify): shipping/tax are only known at the store's checkout.
        # Merchandise alone is a lower bound on the charge, so it can still prove a budget violation.
        merch = unit_price(product, variant) * qty
        if req.get("max_charge_cents") is not None and merch > req["max_charge_cents"]:
            checks.append(_check("BUDGET", "Within your maximum charge", FAIL, f"{fmt_cents(merch)} before shipping & tax",
                                 f"≤ {fmt_cents(req['max_charge_cents'])}", "The item price alone exceeds your limit."))
        checks.append(_check("TOTAL_UNKNOWN", "Total charge known", FAIL, "shipping and tax calculated at checkout",
                             "a verifiable total", "PerkPilot cannot verify this total yet; the store's checkout calculates it."))
        return {"status": FAIL, "checks": checks, "price_breakdown": None, "options": [], "recommended_option": None,
                "guidance": [], "estimated_delivery_date": None}

    # ---- payment method
    shopper = store.get("shoppers", shopper_id) or {}
    pm = store.get("payment_methods", proposal["payment_method_id"])
    pm_ok = bool(pm) and pm["payment_method_id"] in shopper.get("payment_methods", [])
    if pm_ok and req.get("payment_method_id") and req["payment_method_id"] != pm["payment_method_id"]:
        pm_ok = False
    checks.append(_check("PAYMENT_METHOD", "Payment method", PASS if pm_ok else FAIL,
                         pm["label"] if pm else proposal["payment_method_id"],
                         req.get("payment_method_id") or "a simulated method saved to this shopper",
                         "Simulated test payment method on file." if pm_ok else "Payment method is not available for this shopper or differs from your preference."))

    # ---- pricing and offers (server-side, integer cents)
    ship = find_shipping(product, proposal.get("shipping_option", "standard"))
    if not ship:
        checks.append(_check("SHIPPING_OPTION", "Shipping option", FAIL, proposal.get("shipping_option"), "a valid option",
                             "Unknown shipping option."))
        ship = product["delivery_options"][0]
    bd = price_cart(store, product, qty, ship["id"], proposal.get("coupon_codes", []), proposal.get("card_offer_ids", []),
                    proposal["payment_method_id"], proposal["variant_id"] if variant else None)
    if bd["tax_source"] == "unavailable":
        checks.append(_check("TOTAL_UNKNOWN", "Total charge known", FAIL, "Shopify couldn't calculate tax for this cart",
                             "a total calculated by Shopify", "PerkPilot won't approve a charge it can't verify. Try again shortly."))
    elif bd["shopify_totals"]:
        st = bd["shopify_totals"]
        ok = st["total_cents"] == bd["charge_cents"] and st["discount_cents"] == bd["coupon_discount_cents"]
        checks.append(_check("SHOPIFY_TOTAL_MATCH", "Shopify agrees on the total", PASS if ok else FAIL, fmt_cents(st["total_cents"]) + " from Shopify",
                             fmt_cents(bd["charge_cents"]) + " verified",
                             "Shopify calculated the same total for this exact cart, including tax and the discount code." if ok else
                             f"Shopify applied {fmt_cents(st['discount_cents'])} in discounts and totals {fmt_cents(st['total_cents'])}; "
                             "PerkPilot won't hand off a cart whose total it can't reproduce."))
    for ev in bd["coupon_evaluations"]:
        rule = ev.get("rule") or "COUPON_ELIGIBILITY"
        checks.append(_check(rule, f"Coupon {ev['code']}", PASS if ev["eligible"] else FAIL,
                             fmt_cents(-ev["discount_cents"]) if ev["eligible"] else "not eligible",
                             "valid, unexpired, eligible product, minimum spend met", ev["reason"]))
    for ev in bd["card_offer_evaluations"]:
        observed = fmt_cents(ev["observed_cents"]) + " charge" if "observed_cents" in ev else ev.get("observed_payment_method", "n/a")
        required = f"≥ {fmt_cents(ev['threshold_cents'])} {ev['price_basis'].replace('_', ' ')}" if ev.get("threshold_cents") else "enrolled card"
        checks.append(_check("CARD_OFFER_ELIGIBILITY", f"Card-linked offer", PASS if ev["eligible"] else FAIL, observed, required,
                             ev["reason"]))

    if proposal.get("claimed_charge_cents") is not None:
        ok = proposal["claimed_charge_cents"] == bd["charge_cents"]
        checks.append(_check("CLAIMED_CHARGE", "Quoted charge matches", PASS if ok else FAIL,
                             fmt_cents(proposal["claimed_charge_cents"]), fmt_cents(bd["charge_cents"]),
                             "The proposal's charge matches the server-side calculation." if ok else
                             "The proposal quoted a charge that differs from the server-side calculation."))
    if proposal.get("claimed_effective_cost_cents") is not None:
        ok = proposal["claimed_effective_cost_cents"] >= bd["effective_cost_cents"]
        checks.append(_check("CLAIMED_SAVINGS", "Claimed savings are real", PASS if ok else FAIL,
                             fmt_cents(proposal["claimed_effective_cost_cents"]) + " claimed",
                             fmt_cents(bd["effective_cost_cents"]) + " verified effective cost",
                             "Claimed savings are supported." if ok else
                             "The proposal promised savings that the offer terms do not support."))

    if req.get("max_charge_cents") is not None:
        includes = req.get("budget_includes_shipping_and_tax", True)
        observed = bd["charge_cents"] if includes else bd["merchandise_cents"] - bd["coupon_discount_cents"]
        ok = observed <= req["max_charge_cents"]
        basis = "total charge incl. shipping & tax" if includes else "merchandise after coupons"
        checks.append(_check("BUDGET", "Within your maximum charge", PASS if ok else FAIL, f"{fmt_cents(observed)} {basis}",
                             f"≤ {fmt_cents(req['max_charge_cents'])}",
                             f"Charge is {fmt_cents(req['max_charge_cents'] - observed)} under your limit." if ok else
                             f"Charge exceeds your limit by {fmt_cents(observed - req['max_charge_cents'])}. A later statement credit cannot offset today's charge."))

    est = estimate_delivery(store, ship)
    if req.get("deadline_date"):
        deadline = date.fromisoformat(req["deadline_date"])
        ok = est <= deadline
        checks.append(_check("DELIVERY_DEADLINE", "Arrives by your deadline", PASS if ok else FAIL,
                             f"est. {_day(est)}", f"by {_day(deadline)}",
                             f"{ship['label']} is estimated to arrive {est.strftime('%A')}." if ok else
                             f"{ship['label']} is estimated to arrive {est:%A %b} {est.day}, after your deadline."))

    if bd["later_statement_credit_cents"]:
        checks.append(_check("CREDIT_TIMING", "Statement credit timing", INFO, fmt_cents(bd["later_statement_credit_cents"]) + " later",
                             "not a checkout discount",
                             "This credit would post after settlement. It does not reduce today's charge.", blocking=False))

    guidance = []
    if variant and apply_learned_rules:
        for rule in active_rules_for(store, product["product_id"], variant["size"]):
            target = next((v for v in product["variants"] if v["color"] == variant["color"] and v["size"] == rule["proposed_action"]["suggest_size"]), None)
            guidance.append({
                "rule_id": rule["rule_id"],
                "message": rule["customer_message"],
                "suggest_size": rule["proposed_action"]["suggest_size"],
                "suggest_variant_id": target["variant_id"] if target else None,
                "suggest_in_stock": bool(target and product["inventory_by_variant"].get(target["variant_id"], 0) > 0),
                "evidence": f"{rule['confirmed_outcome_count']} confirmed fit exchanges among {rule['affected_order_count']} fulfilled size-{variant['size']} orders",
                "approved_by": rule.get("approval_actor"),
            })
            checks.append(_check("LEARNED_FIT_GUIDANCE", "Merchant-approved fit guidance", WARN, f"size {variant['size']}",
                                 f"consider size {rule['proposed_action']['suggest_size']}",
                                 rule["customer_message"] + " Your selection will not be changed unless you choose to.",
                                 blocking=False))

    status = FAIL if any(c["blocking"] and c["status"] == FAIL for c in checks) else PASS
    result = {
        "status": status,
        "checks": checks,
        "price_breakdown": bd,
        "estimated_delivery_date": est.isoformat(),
        "guidance": guidance,
        "options": [],
        "recommended_option": None,
        "evaluated_at": now(store).isoformat(),
    }
    if with_options and (status == FAIL or proposal.get("claimed_effective_cost_cents") is not None):
        opts = build_options(store, req, product, shopper_id, proposal, apply_learned_rules)
        result["options"] = opts
        passing = [o for o in opts if o["status"] == PASS]
        result["recommended_option"] = passing[0]["option_id"] if passing else None
    return result


def candidate_proposals(store: BaseStore, req: dict, product: dict, payment_method_id: str) -> list[dict]:
    """Every honest cart for the requested variant: shipping x (no coupon | one coupon), card offers only if eligible."""
    variant = matching_variant(product, req)
    if not variant:
        return []
    qty = req.get("quantity", 1)
    coupons = [o["code"] for o in store.find("offers", {"offer_type": "coupon"}) if o["status"] == "active"]
    card_offers = [o["offer_id"] for o in store.find("offers", {"offer_type": "card_linked_statement_credit"})]
    out = []
    for ship, coupon in cartesian(product["delivery_options"], [None, *coupons]):
        codes = [coupon] if coupon else []
        bd = price_cart(store, product, qty, ship["id"], codes, card_offers, payment_method_id, variant["variant_id"])
        if coupon and not bd["coupon_evaluations"][0]["eligible"]:
            continue
        eligible_cards = [ev["offer_id"] for ev in bd["card_offer_evaluations"] if ev["eligible"]]
        out.append({"product_id": product["product_id"], "variant_id": variant["variant_id"], "quantity": qty,
                    "shipping_option": ship["id"], "coupon_codes": codes, "card_offer_ids": eligible_cards,
                    "payment_method_id": payment_method_id, "source": "corrected_option"})
    return out


def _option_summary(store, prop: dict, res: dict, req: dict) -> dict:
    bd = res["price_breakdown"]
    card_info = next(iter(bd["card_offer_evaluations"]), None)
    all_cards = price_cart(store, store.get("products", prop["product_id"]), prop["quantity"], prop["shipping_option"],
                           prop["coupon_codes"], [o["offer_id"] for o in store.find("offers", {"offer_type": "card_linked_statement_credit"})],
                           prop["payment_method_id"], prop["variant_id"])["card_offer_evaluations"]
    card_info = all_cards[0] if all_cards else card_info
    fails = [c for c in res["checks"] if c["status"] == FAIL]
    return {
        "proposal": prop,
        "status": res["status"],
        "charge_cents": bd["charge_cents"],
        "later_statement_credit_cents": bd["later_statement_credit_cents"],
        "effective_cost_cents": bd["effective_cost_cents"],
        "coupon": prop["coupon_codes"][0] if prop["coupon_codes"] else None,
        "coupon_discount_cents": bd["coupon_discount_cents"],
        "shipping_label": bd["shipping_label"],
        "estimated_delivery_date": res["estimated_delivery_date"],
        "card_offer": {"eligible": card_info["eligible"], "reason": card_info["reason"]} if card_info else None,
        "within_budget": req.get("max_charge_cents") is None or bd["charge_cents"] <= req["max_charge_cents"],
        "failed_rules": [c["rule"] for c in fails],
        "failure_summary": "; ".join(c["explanation"] for c in fails),
    }


def build_options(store: BaseStore, req: dict, product: dict, shopper_id: str, base: dict, apply_learned_rules: bool) -> list[dict]:
    pm = base.get("payment_method_id") or ("pm_shopify_visa" if product.get("source") == "shopify" else "pm_visa_4242")
    evaluated = []
    for prop in candidate_proposals(store, req, product, pm):
        res = verify(store, req, prop, shopper_id, apply_learned_rules=apply_learned_rules, with_options=False)
        evaluated.append(_option_summary(store, prop, res, req))
    passing = sorted([o for o in evaluated if o["status"] == PASS], key=lambda o: (o["charge_cents"], o["effective_cost_cents"]))
    chosen = passing[:1]
    # Show the instructive trade-off: same shipping as the original proposal, without a coupon, when it differs.
    same_ship = [o for o in evaluated if o["proposal"]["shipping_option"] == base.get("shipping_option", "standard")]
    contrast = next((o for o in same_ship if o not in chosen and o["coupon"] is None), None)
    if contrast:
        chosen.append(contrast)
    elif len(passing) > 1:
        chosen.append(passing[1])
    if not chosen:
        chosen = sorted(evaluated, key=lambda o: o["charge_cents"])[:2]
    for i, o in enumerate(chosen):
        o["option_id"] = "ABCD"[i]
        bits = [f"Use {o['coupon']}" if o["coupon"] else "No coupon", o["shipping_label"]]
        o["title"] = " · ".join(bits)
    return chosen


def naive_agent_proposal(store: BaseStore, req: dict, product: dict, payment_method_id: str) -> dict | None:
    """The demo's *deliberately flawed* shopping agent.

    It picks the best single coupon and then checks card-linked offers against the
    pre-discount total instead of the offer's declared basis (the actual charge). That is
    exactly the kind of plausible-looking mistake the verifier exists to catch.
    """
    variant = matching_variant(product, req)
    if not variant:
        return None
    ship = product["delivery_options"][0]
    qty = req.get("quantity", 1)
    best = None
    for o in store.find("offers", {"offer_type": "coupon"}):
        bd = price_cart(store, product, qty, ship["id"], [o["code"]], [], payment_method_id, variant["variant_id"])
        ev = bd["coupon_evaluations"][0]
        if ev["eligible"] and (best is None or ev["discount_cents"] > best[1]):
            best = (o["code"], ev["discount_cents"])
    codes = [best[0]] if best else []
    bd = price_cart(store, product, qty, ship["id"], codes, [], payment_method_id, variant["variant_id"])
    claimed_cards, credit = [], 0
    for o in store.find("offers", {"offer_type": "card_linked_statement_credit"}):
        if payment_method_id in o["applicable_payment_methods"] and bd["pre_discount_total_cents"] >= o["minimum_spend_cents"]:
            claimed_cards.append(o["offer_id"])
            credit += o["amount_cents"]
    return {"product_id": product["product_id"], "variant_id": variant["variant_id"], "quantity": qty,
            "shipping_option": ship["id"], "coupon_codes": codes, "card_offer_ids": claimed_cards,
            "payment_method_id": payment_method_id, "claimed_charge_cents": bd["charge_cents"],
            "claimed_effective_cost_cents": bd["charge_cents"] - credit, "source": "agent"}


def coupon_known(store: BaseStore, code: str) -> bool:
    return offer_by_code(store, code) is not None
