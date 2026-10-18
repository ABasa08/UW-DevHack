"""Shopping workflow: understand -> confirm -> recommend -> verify -> authorize."""

from __future__ import annotations

from .ai import parse_shopping_request
from .core import DomainError, MERCHANT_ID, append_event, fmt_cents, merchant_name, next_id, now, sha256_hex, shopify_mode, today
from .models import Requirements
from .pricing import catalog_facets, estimate_delivery, find_variant, size_label, unit_price
from .records import make_purchase_record, make_shipment, process_shipments
from .store import BaseStore
from .verifier import FAIL, PASS, build_options, matching_variant, naive_agent_proposal, verify


def _shopper(store: BaseStore, shopper_id: str) -> dict:
    s = store.get("shoppers", shopper_id)
    if not s:
        raise DomainError(f"Unknown shopper '{shopper_id}'.", 404, "not_found")
    return s


def parse_intent(store: BaseStore, shopper_id: str, text: str) -> dict:
    _shopper(store, shopper_id)
    titles = {p["name"]: p["category"] for p in store.find("products", {"status": "active"})}
    parsed = parse_shopping_request(text, today(store), {**catalog_facets(store), "titles": titles})
    brand = parsed["requirements"].get("brand")
    if brand and not any(brand_matches(p["brand"], brand) for p in store.find("products", {"status": "active"})):
        parsed["notes"].append(f"{merchant_name(store)} doesn't carry {brand}. Brands available here: {', '.join(carried_brands(store))}. "
                               "You can clear the brand below to see similar shoes.")
    intent_id = next_id(store, "int")
    intent = {
        "_id": intent_id, "intent_id": intent_id, "shopper_id": shopper_id, "merchant_id": MERCHANT_ID,
        "original_request": text, "parsed_requirements": parsed["requirements"], "ambiguities": parsed["ambiguities"],
        "interpreter": parsed["interpreter"], "assistant_message": parsed["assistant_message"], "notes": parsed["notes"],
        "confirmed_requirements": None, "confirmation_time": None, "confirmation_version": 0,
        "confirmation_history": [], "status": "parsed", "created_at": now(store).isoformat(),
    }
    store.put("intents", intent)
    append_event(store, subject_type="intent", subject_id=intent_id, event_type="intent.parsed", actor_type="ai_interpreter",
                 payload={"interpreter": parsed["interpreter"]["mode"], "missing": parsed["missing_essential"]})
    return {**intent, "missing_essential": parsed["missing_essential"]}


def get_intent(store: BaseStore, intent_id: str) -> dict:
    intent = store.get("intents", intent_id)
    if not intent:
        raise DomainError(f"Unknown intent '{intent_id}'.", 404, "not_found")
    return intent


def confirm_intent(store: BaseStore, intent_id: str, req: Requirements, change_reason: str | None) -> dict:
    intent = get_intent(store, intent_id)
    if intent["status"] == "purchased":
        raise DomainError("This request already resulted in a purchase. Start a new request to shop again.", 409, "invalid_state")
    facets = catalog_facets(store)
    if req.category and req.category not in facets["categories"]:
        raise DomainError(f"This store doesn't sell '{req.category}'. Choose one of: {', '.join(facets['categories'])}.", 422,
                          "unknown_category", facets["categories"])
    # Size is essential only where the catalog has sizes (a candle has none).
    needs_size = req.category is None or req.category in facets["sized_categories"]
    missing = [f for f in ("category", "size", "max_charge_cents") if getattr(req, f) is None and (f != "size" or needs_size)]
    if missing:
        raise DomainError(f"Please confirm these required fields first: {', '.join(missing)}.", 422, "missing_fields", missing)
    if req.payment_method_id:
        shopper = _shopper(store, intent["shopper_id"])
        if req.payment_method_id not in shopper["payment_methods"]:
            raise DomainError("That payment method isn't saved for this shopper.", 422, "invalid_payment_method")
    if req.deadline_date and req.deadline_date < today(store).isoformat():
        raise DomainError("The delivery deadline is in the past.", 422, "invalid_deadline")
    confirmed = req.model_dump()
    version = intent["confirmation_version"] + 1
    intent.update(confirmed_requirements=confirmed, confirmation_time=now(store).isoformat(), confirmation_version=version,
                  status="confirmed")
    intent["confirmation_history"].append({"version": version, "at": intent["confirmation_time"], "requirements": confirmed,
                                           "reason": change_reason or ("initial confirmation" if version == 1 else "edited by shopper")})
    store.put("intents", intent)
    append_event(store, subject_type="intent", subject_id=intent_id, event_type="intent.confirmed", actor_type="shopper",
                 payload={"version": version, "requirements_hash": sha256_hex(confirmed), "reason": change_reason})
    return intent


def _confirmed(intent: dict) -> dict:
    if intent["status"] not in ("confirmed", "checkout_started", "purchased") or not intent["confirmed_requirements"]:
        raise DomainError("Confirm your requirements before PerkPilot can recommend or verify anything.", 409, "intent_not_confirmed")
    return intent["confirmed_requirements"]


def brand_matches(product_brand: str, wanted: str) -> bool:
    a, b = product_brand.strip().lower(), wanted.strip().lower()
    return a == b or a in b or b in a


def carried_brands(store: BaseStore) -> list[str]:
    return sorted({p["brand"] for p in store.find("products", {"status": "active"})})


def _evaluate_candidates(store: BaseStore, req: dict, shopper_id: str, pm: str, apply_learned_rules: bool) -> list[dict]:
    candidates = []
    for product in store.find("products", {"status": "active"}):
        if req.get("category") and product["category"] != req["category"]:
            continue
        variant = matching_variant(product, req)
        if req.get("brand") and not brand_matches(product["brand"], req["brand"]):
            reason = f"Brand is {product['brand']}, not {req['brand']}"
            if not variant:
                reason += f"; also not offered in {req.get('color') or ''} size {req.get('size')}".replace("  ", " ")
            candidates.append({"product_id": product["product_id"], "name": product["name"], "eligible": False,
                               "reasons": [reason], "price_cents": unit_price(product, variant), "best_option": None})
            continue
        if not variant:
            colors = sorted({v["color"] for v in product["variants"] if not req.get("size") or v["size"] == req["size"]})
            reason = f"Not offered in {req.get('color') or ''} size {req.get('size')}".replace("  ", " ")
            if colors and req.get("color"):
                reason += f" (size {req['size']} comes in {', '.join(colors)})"
            candidates.append({"product_id": product["product_id"], "name": product["name"], "eligible": False, "reasons": [reason],
                               "price_cents": unit_price(product, None), "best_option": None})
            continue
        base = {"shipping_option": "standard", "payment_method_id": pm}
        if not product["delivery_options"]:
            # Platform product: verify everything knowable here, then hand off to the store's own checkout for the total.
            res = verify(store, req, {**base, "product_id": product["product_id"], "variant_id": variant["variant_id"],
                                      "quantity": req.get("quantity", 1), "coupon_codes": [], "card_offer_ids": []},
                         shopper_id, apply_learned_rules=apply_learned_rules, with_options=False)
            fails = [c for c in res["checks"] if c["status"] == FAIL and c["rule"] != "TOTAL_UNKNOWN"]
            candidates.append({"product_id": product["product_id"], "name": product["name"], "eligible": False,
                               "reasons": [c["explanation"] for c in fails] or
                                          ["Options, stock and item price check out. Shipping and tax are calculated at the store's checkout."],
                               "price_cents": unit_price(product, variant), "variant_id": variant["variant_id"], "best_option": None,
                               "checkout_url": None if fails else variant.get("checkout_url")})
            continue
        opts = build_options(store, req, product, shopper_id, base, apply_learned_rules)
        passing = [o for o in opts if o["status"] == PASS]
        best = passing[0] if passing else (opts[0] if opts else None)
        reasons = []
        if not passing:
            if product["inventory_by_variant"].get(variant["variant_id"], 0) < req.get("quantity", 1):
                reasons.append(f"Out of stock in {variant['color']} {size_label(variant['size'])}".replace("  ", " "))
            elif best:
                reasons.append(best["failure_summary"] or "No option satisfies your requirements")
        candidates.append({"product_id": product["product_id"], "name": product["name"], "eligible": bool(passing),
                           "reasons": reasons, "price_cents": unit_price(product, variant), "variant_id": variant["variant_id"],
                           "best_option": best})
    return candidates


RELAXABLE = [("brand", "Search without the brand"), ("color", "Search any color"), ("deadline_date", "Search without the delivery deadline")]


def _no_match_help(store: BaseStore, req: dict, shopper_id: str, pm: str, apply_learned_rules: bool) -> tuple[list[str], list[dict]]:
    """When nothing qualifies: explain why, and offer single-field relaxations that would actually find something."""
    notices = []
    if req.get("brand") and not any(brand_matches(p["brand"], req["brand"]) for p in store.find("products", {"status": "active"})):
        notices.append(f"{merchant_name(store)} doesn't carry {req['brand']}. Brands available here: {', '.join(carried_brands(store))}.")
    def attempt(fields: list[str], label: str) -> dict | None:
        relaxed = {**req, **{f: None for f in fields}, **({"deadline_expression": None} if "deadline_date" in fields else {})}
        found = sorted([c for c in _evaluate_candidates(store, relaxed, shopper_id, pm, apply_learned_rules) if c["eligible"]],
                       key=lambda c: c["best_option"]["charge_cents"])
        if not found:
            return None
        return {"fields": fields, "label": label, "finds": found[0]["name"], "count": len(found),
                "charge_cents": found[0]["best_option"]["charge_cents"]}

    present = [(f, label) for f, label in RELAXABLE if req.get(f)]
    relaxations = [r for r in (attempt([f], label) for f, label in present) if r]
    if not relaxations and len(present) > 1:
        names = {"brand": "brand", "color": "color", "deadline_date": "deadline"}
        combo = attempt([f for f, _ in present], "Search without the " + " and ".join(names[f] for f, _ in present))
        if combo:
            relaxations.append(combo)
    return notices, relaxations


def recommend(store: BaseStore, intent_id: str, apply_learned_rules: bool = True) -> dict:
    intent = get_intent(store, intent_id)
    req = _confirmed(intent)
    shopper = _shopper(store, intent["shopper_id"])
    pm = req.get("payment_method_id") or shopper["default_payment_method"]
    candidates = _evaluate_candidates(store, req, intent["shopper_id"], pm, apply_learned_rules)
    eligible = sorted([c for c in candidates if c["eligible"]], key=lambda c: (c["best_option"]["charge_cents"], c["name"]))
    top = eligible[0] if eligible else None
    agent = None
    guidance = []
    notices, relaxations = ([], []) if top else _no_match_help(store, req, intent["shopper_id"], pm, apply_learned_rules)
    if top:
        product = store.get("products", top["product_id"])
        agent = naive_agent_proposal(store, req, product, pm)
        pre = verify(store, req, {**agent, "claimed_charge_cents": None, "claimed_effective_cost_cents": None, "card_offer_ids": []},
                     intent["shopper_id"], apply_learned_rules=apply_learned_rules, with_options=False)
        guidance = pre["guidance"]
    ranked = eligible + sorted([c for c in candidates if not c["eligible"]], key=lambda c: (not c.get("checkout_url"), c["name"]))
    explanation = None
    if agent:
        credit = agent["claimed_charge_cents"] - agent["claimed_effective_cost_cents"]
        explanation = (f"I found the {top['name']} in {req['color'] + ' ' if req.get('color') else ''}{size_label(req.get('size'))}. "
                       f"With {', '.join(agent['coupon_codes']) or 'no coupon'} the charge is {fmt_cents(agent['claimed_charge_cents'])}"
                       + (f", and your Visa card-linked offer should take off another {fmt_cents(credit)} — "
                          f"an effective cost of {fmt_cents(agent['claimed_effective_cost_cents'])}." if credit else "."))
    append_event(store, subject_type="intent", subject_id=intent_id, event_type="recommendations.generated", actor_type="system",
                 payload={"top": top["product_id"] if top else None, "learned_rules_applied": apply_learned_rules,
                          "guidance_rule_ids": [g["rule_id"] for g in guidance]})
    return {
        "intent_id": intent_id, "confirmed_requirements": req, "candidates": ranked, "top_pick": top,
        "agent_proposal": agent, "agent_message": explanation,
        "agent_disclosure": ("Proposal drafted by the demo's naive agent planner, which evaluates card-offer thresholds "
                             "before discounts. Every proposal must pass the deterministic verifier before checkout."),
        "guidance": guidance, "learned_rules_applied": apply_learned_rules,
        "notices": notices, "relaxations": relaxations,
    }


def verify_proposal(store: BaseStore, intent_id: str, proposal: dict) -> dict:
    intent = get_intent(store, intent_id)
    req = _confirmed(intent)
    if intent["status"] == "purchased":
        raise DomainError("This request has already been purchased.", 409, "invalid_state")
    result = verify(store, req, proposal, intent["shopper_id"])
    quote_id = next_id(store, "qt")
    quote = {
        "_id": quote_id, "quote_id": quote_id, "intent_id": intent_id, "intent_version": intent["confirmation_version"],
        "shopper_id": intent["shopper_id"], "merchant_id": MERCHANT_ID, "proposal": proposal, "result": result,
        "status": "open" if result["status"] == PASS else "blocked", "created_at": now(store).isoformat(),
    }
    quote["quote_hash"] = sha256_hex({"proposal": proposal, "breakdown": result["price_breakdown"], "intent_version": quote["intent_version"]})
    store.put("quotes", quote)
    failed = [c["rule"] for c in result["checks"] if c["status"] == FAIL]
    append_event(store, subject_type="intent", subject_id=intent_id, event_type="checkout.verified", actor_type="verifier",
                 payload={"quote_id": quote_id, "status": result["status"], "failed_rules": failed,
                          "proposal_source": proposal.get("source"),
                          "charge_cents": result["price_breakdown"]["charge_cents"] if result["price_breakdown"] else None})
    return quote


def get_quote(store: BaseStore, quote_id: str) -> dict:
    q = store.get("quotes", quote_id)
    if not q:
        raise DomainError(f"Unknown quote '{quote_id}'.", 404, "not_found")
    return q


def confirm_checkout(store: BaseStore, quote_id: str, shopper_id: str, approved_charge_cents: int, shopper_approved: bool) -> dict:
    quote = get_quote(store, quote_id)
    if quote["shopper_id"] != shopper_id:
        raise DomainError("This quote belongs to a different shopper.", 403, "forbidden")
    if quote["status"] == "used":
        existing = store.find_one("purchases", {"quote_id": quote_id})
        return {"status": "ALREADY_CONFIRMED", "purchase": existing, "duplicate": True}
    if quote["status"] == "handed_off":
        return {"status": "AWAITING_PAYMENT", "purchase": None, "duplicate": True,
                "handoff": public_handoff(store.find_one("handoffs", {"quote_id": quote_id}))}
    if not shopper_approved:
        raise DomainError("Explicit shopper approval is required to place this order.", 400, "approval_required")
    if quote["status"] != "open" or quote["result"]["status"] != PASS:
        raise DomainError("This proposal did not pass verification and cannot be purchased.", 409, "verification_failed")
    intent = get_intent(store, quote["intent_id"])
    if intent["confirmation_version"] != quote["intent_version"]:
        quote["status"] = "superseded"
        store.put("quotes", quote)
        raise DomainError("Your requirements changed after this quote. Please re-verify and approve again.", 409,
                          "NEEDS_RECONFIRMATION")

    # Critical re-verification at the moment of purchase against *current* catalog/offer data.
    rerun = verify(store, intent["confirmed_requirements"], quote["proposal"], shopper_id, with_options=False)
    new_charge = rerun["price_breakdown"]["charge_cents"] if rerun["price_breakdown"] else None
    if rerun["status"] == FAIL:
        quote["status"] = "superseded"
        store.put("quotes", quote)
        append_event(store, subject_type="intent", subject_id=intent["intent_id"], event_type="checkout.blocked_at_confirm",
                     actor_type="verifier", payload={"quote_id": quote_id, "failed_rules": [c["rule"] for c in rerun["checks"] if c["status"] == FAIL]})
        raise DomainError("Something changed since you approved: the order no longer passes verification.", 409, "verification_failed",
                          {"verification": rerun})
    if new_charge != quote["result"]["price_breakdown"]["charge_cents"] or approved_charge_cents != new_charge:
        quote["status"] = "superseded"
        store.put("quotes", quote)
        new_quote = verify_proposal(store, intent["intent_id"], quote["proposal"])
        append_event(store, subject_type="intent", subject_id=intent["intent_id"], event_type="checkout.reconfirmation_required",
                     actor_type="verifier", payload={"quote_id": quote_id, "approved_charge_cents": approved_charge_cents,
                                                     "current_charge_cents": new_charge, "new_quote_id": new_quote["quote_id"]})
        raise DomainError(f"The charge is now {fmt_cents(new_charge)}, not the {fmt_cents(approved_charge_cents)} you approved. "
                          "Please review and approve the updated amount.", 409, "NEEDS_RECONFIRMATION",
                          {"new_quote": new_quote})

    product = store.get("products", quote["proposal"]["product_id"])
    variant = find_variant(product, quote["proposal"]["variant_id"])
    qty = quote["proposal"]["quantity"]
    if shopify_mode() and product.get("source") == "shopify":
        return _hand_off_to_shopify(store, quote, intent, product, variant, rerun, shopper_id)
    product["inventory_by_variant"][variant["variant_id"]] -= qty
    store.put("products", product)

    purchase_id = next_id(store, "pur")
    auth = append_event(store, subject_type="purchase", subject_id=purchase_id, purchase_id=purchase_id,
                        event_type="purchase.authorized_by_shopper", actor_type="shopper",
                        payload={"quote_id": quote_id, "quote_hash": quote["quote_hash"], "approved_charge_cents": approved_charge_cents,
                                 "intent_version": intent["confirmation_version"]})
    txn = next_id(store, "SIM-TXN")
    ship = next(o for o in product["delivery_options"] if o["id"] == quote["proposal"]["shipping_option"])
    est = estimate_delivery(store, ship)
    record = make_purchase_record(
        store, purchase_id=purchase_id, shopper_id=shopper_id, product=product, variant=variant, quantity=qty,
        breakdown=rerun["price_breakdown"], payment_method_id=quote["proposal"]["payment_method_id"],
        approved_delivery_date=est, verification_checks=rerun["checks"], simulated_transaction_id=txn,
        created_at=now(store), intent=intent, authorization_event_id=auth["event_id"],
    )
    record["quote_id"] = quote_id  # lookup aid only; not part of the hashed record
    store.put("purchases", record)
    append_event(store, subject_type="purchase", subject_id=purchase_id, purchase_id=purchase_id, event_type="payment.authorized",
                 actor_type="payment_simulator", payload={"simulated_transaction_id": txn, "amount_cents": approved_charge_cents,
                                                          "simulated": True, "network": "Visa (simulated)"})
    append_event(store, subject_type="purchase", subject_id=purchase_id, purchase_id=purchase_id, event_type="purchase.confirmed",
                 actor_type="system", payload={"record_hash": record["record_hash"], "approved_delivery_date": est.isoformat()})
    append_event(store, subject_type="purchase", subject_id=purchase_id, purchase_id=purchase_id, event_type="payment.settled",
                 actor_type="payment_simulator", payload={"amount_cents": approved_charge_cents, "simulated": True})
    make_shipment(store, shipment_id=next_id(store, "shp"), purchase_id=purchase_id, variant_id=variant["variant_id"],
                  placed_at=now(store), expected=est)
    process_shipments(store)
    quote["status"] = "used"
    quote["purchase_id"] = purchase_id
    store.put("quotes", quote)
    intent["status"] = "purchased"
    store.put("intents", intent)
    return {"status": "CONFIRMED", "purchase": record, "duplicate": False}


def public_handoff(h: dict | None) -> dict | None:
    """What the shopper's browser needs to finish paying; draft-order internals stay server-side."""
    if not h:
        return None
    return {k: h.get(k) for k in ("purchase_id", "status", "checkout_url", "order_name", "approved_at")} | {
        "approved_charge_cents": h["breakdown"]["charge_cents"], "draft_order_name": h["draft_order_name"]}


def _hand_off_to_shopify(store: BaseStore, quote: dict, intent: dict, product: dict, variant: dict, verification: dict,
                         shopper_id: str) -> dict:
    """Shopify takes payment: re-read live stock, create the exact draft order, and send the shopper to Shopify checkout."""
    from .shopify_commerce import begin_handoff, live_stock

    try:
        stock = live_stock([variant])[variant["variant_id"]]
    except Exception:  # noqa: BLE001 - the draft order's inventory reservation still guards the sale
        stock = None
    if stock is not None and stock < quote["proposal"]["quantity"]:
        quote["status"] = "superseded"
        store.put("quotes", quote)
        raise DomainError("Shopify shows this variant just sold out. Please choose another option.", 409, "inventory_conflict")
    h = begin_handoff(store, quote=quote, intent=intent, product=product, variant=variant, verification=verification,
                      shopper=_shopper(store, shopper_id))
    quote["status"] = "handed_off"
    quote["purchase_id"] = h["purchase_id"]
    store.put("quotes", quote)
    intent["status"] = "checkout_started"
    store.put("intents", intent)
    return {"status": "AWAITING_PAYMENT", "purchase": None, "duplicate": False, "handoff": public_handoff(h)}
