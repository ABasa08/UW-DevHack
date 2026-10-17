"""Post-purchase resolution: complaint intake, evidence-based classification, remedies."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .ai import parse_complaint
from .core import DomainError, MERCHANT_ID, append_event, fmt_cents, next_id, now
from .learning import evaluate_group, group_stats
from .pricing import estimate_delivery, find_variant
from .records import make_purchase_record, make_shipment, process_shipments, shipment_for
from .store import BaseStore
from .verifier import FAIL, INFO, PASS, _check

ACTIVE_CLAIM_STATUSES = {"open", "needs_info", "in_review", "exchange_in_progress", "awaiting_fit_confirmation"}


# ---------------------------------------------------------------- helpers


def _purchase(store: BaseStore, purchase_id: str, shopper_id: str | None = None) -> dict:
    p = store.get("purchases", purchase_id)
    if not p:
        raise DomainError(f"Unknown purchase '{purchase_id}'.", 404, "not_found")
    if shopper_id and p["shopper_id"] != shopper_id:
        raise DomainError("This purchase belongs to a different shopper.", 403, "forbidden")
    return p


def _claim(store: BaseStore, claim_id: str) -> dict:
    c = store.get("claims", claim_id)
    if not c:
        raise DomainError(f"Unknown claim '{claim_id}'.", 404, "not_found")
    return c


def settled_amount(store: BaseStore, purchase_id: str) -> int | None:
    evs = [e for e in store.find("events", {"subject_id": purchase_id, "event_type": "payment.settled"})]
    if not evs:
        return None
    return sorted(evs, key=lambda e: e["seq"])[-1]["payload"]["amount_cents"]


def _variant_label(product: dict, variant_id: str) -> str:
    v = find_variant(product, variant_id)
    return f"{v['color'].title()} / US {v['size']} ({v['sku']})" if v else variant_id


# ---------------------------------------------------------------- classification


def classify(store: BaseStore, purchase: dict, issue: dict, exclude_claim_id: str | None = None) -> dict:
    """Deterministic: the same evidence always yields the same classification."""
    product = store.get("products", purchase["product_id"])
    sh = shipment_for(store, purchase["purchase_id"])
    delivered = bool(sh and sh.get("delivered_at"))
    settled = settled_amount(store, purchase["purchase_id"])
    evidence = [
        {"type": "purchase_record", "ref": purchase["purchase_id"], "status": "confirmed",
         "summary": f"Shopper approved {_variant_label(product, purchase['variant_id'])}, charge {fmt_cents(purchase['approved_charge_cents'])}, "
                    f"delivery by {purchase['approved_delivery_date']}. Record hash {purchase['record_hash'][:12]}…"},
    ]
    if sh:
        if delivered:
            evidence.append({"type": "fulfillment", "ref": sh["shipment_id"], "status": "confirmed",
                             "summary": f"Carrier record: delivered {_variant_label(product, sh['delivered_variant_id'])} on {sh['actual_delivery_date']}."})
        else:
            evidence.append({"type": "fulfillment", "ref": sh["shipment_id"], "status": "confirmed",
                             "summary": f"Not delivered yet (status: {sh['status']}); expected {sh['expected_delivery_date']}."})
    if settled is not None:
        evidence.append({"type": "payment", "ref": purchase["simulated_transaction_id"], "status": "confirmed",
                         "summary": f"Simulated settlement of {fmt_cents(settled)} against approved {fmt_cents(purchase['approved_charge_cents'])}."})
    evidence.append({"type": "customer_report", "ref": "complaint", "status": "reported",
                     "summary": f"Shopper reports: {issue['problem_type'].replace('_', ' ')}."})

    def out(cls, reason, responsibility="undetermined", follow_up=None, extra=None):
        return {"classification": cls, "classification_reason": reason, "evidence_references": evidence,
                "responsibility": responsibility, "follow_up_question": follow_up,
                "requires_review": cls == "NEEDS_REVIEW", **(extra or {})}

    pt = issue["problem_type"]
    if delivered and sh["delivered_variant_id"] != purchase["variant_id"]:
        return out("ORDER_MISMATCH",
                   f"Fulfillment evidence shows {_variant_label(product, sh['delivered_variant_id'])} was delivered, but the confirmed order was "
                   f"{_variant_label(product, purchase['variant_id'])}.", "merchant_fulfillment_error",
                   extra={"evidence_status": "confirmed_by_records"})
    if pt in ("wrong_item", "wrong_color", "wrong_size"):
        if not delivered:
            return out("NEEDS_REVIEW", "The order hasn't been delivered according to the carrier record.",
                       follow_up="Our records show this order hasn't been delivered yet. Is this about a different order?")
        return out("NEEDS_REVIEW",
                   "The shopper reports a different item, but the fulfillment record shows the confirmed SKU was delivered. Evidence conflicts.",
                   follow_up=f"Our fulfillment record shows {_variant_label(product, purchase['variant_id'])} was delivered. "
                             "What color and size are printed on the label inside the tongue?",
                   extra={"evidence_status": "conflicting"})
    if pt == "charge_issue":
        if settled is not None and settled != purchase["approved_charge_cents"]:
            return out("PRICE_OR_DELIVERY_MISMATCH",
                       f"Settled amount {fmt_cents(settled)} differs from the approved {fmt_cents(purchase['approved_charge_cents'])} "
                       f"by {fmt_cents(settled - purchase['approved_charge_cents'])}.", "merchant_billing_error",
                       extra={"mismatch_kind": "price", "overcharge_cents": settled - purchase["approved_charge_cents"],
                              "evidence_status": "confirmed_by_records"})
        return out("NEEDS_REVIEW", "The simulated settlement matches the approved amount.",
                   follow_up=f"We see one charge of {fmt_cents(purchase['approved_charge_cents'])}, matching what you approved. "
                             "Can you tell us the amount and date of the charge you're seeing?",
                   extra={"evidence_status": "conflicting"})
    if pt == "late_delivery":
        promised = date.fromisoformat(purchase["approved_delivery_date"])
        if delivered and date.fromisoformat(sh["actual_delivery_date"]) > promised:
            days = (date.fromisoformat(sh["actual_delivery_date"]) - promised).days
            return out("PRICE_OR_DELIVERY_MISMATCH", f"Delivered {sh['actual_delivery_date']}, {days} day(s) after the promised {promised}.",
                       "merchant_delivery_promise", extra={"mismatch_kind": "delivery", "days_late": days, "evidence_status": "confirmed_by_records"})
        if not delivered and now(store).date() > promised:
            return out("PRICE_OR_DELIVERY_MISMATCH", f"Still not delivered after the promised {promised}.", "merchant_delivery_promise",
                       extra={"mismatch_kind": "delivery", "evidence_status": "confirmed_by_records"})
        return out("NEEDS_REVIEW", "Carrier records show delivery within the promised date.",
                   follow_up="Our carrier record shows on-time delivery. Did the package arrive somewhere unexpected?",
                   extra={"evidence_status": "conflicting"})
    if pt in ("fit_tight", "fit_loose"):
        if not delivered:
            return out("NEEDS_REVIEW", "Fit can't be assessed before delivery.", follow_up="Has your order arrived yet?")
        stats = group_stats(store, purchase["product_id"], purchase["size"], exclude_claim_id=exclude_claim_id)
        prior = stats["validated_outcome_count"]
        pattern = (f"{prior} prior validated fit exchange(s) for this product in size {purchase['size']} among "
                   f"{stats['fulfilled_order_count']} fulfilled orders")
        evidence.append({"type": "prior_outcomes", "ref": ",".join(stats["validated_claim_ids"]) or "none", "status": "confirmed",
                         "summary": pattern + "."})
        reason = (f"Delivered size {purchase['size']} matches the confirmed order, so this is not an order error. "
                  f"The shopper reports a {'tight' if pt == 'fit_tight' else 'loose'} fit; {pattern}. "
                  "This is a suspected fit issue, not a proven product defect.")
        return out("SUSPECTED_PRODUCT_FIT", reason, "merchant_goodwill_policy",
                   extra={"prior_validated_outcomes": prior, "recurring_pattern_suspected": prior >= 2,
                          "evidence_status": "customer_report_with_supporting_history" if prior else "customer_report_only"})
    if pt == "preference":
        return out("CUSTOMER_PREFERENCE", "The delivered item matches the confirmed order; the shopper's preference changed.",
                   "customer", extra={"evidence_status": "customer_report_only"})
    if pt == "damaged":
        return out("NEEDS_REVIEW", "Damage reports need a photo before a determination.",
                   follow_up="Could you describe where the damage is, or upload a photo for the merchant to review?")
    return out("NEEDS_REVIEW", "The complaint doesn't say what's wrong, so PerkPilot won't guess a cause.",
               follow_up="Sorry to hear that. What's different from what you expected: the fit, the color, the item, the price, or the delivery?",
               extra={"evidence_status": "insufficient"})


# ---------------------------------------------------------------- claims


def create_claim(store: BaseStore, purchase_id: str, shopper_id: str, complaint_text: str) -> dict:
    purchase = _purchase(store, purchase_id, shopper_id)
    if purchase["kind"] != "order":
        raise DomainError("Please report problems on the original order.", 409, "invalid_state")
    for c in store.find("claims", {"purchase_id": purchase_id}):
        if c["status"] in ACTIVE_CLAIM_STATUSES:
            raise DomainError("There's already an open issue for this order.", 409, "claim_exists", {"claim_id": c["claim_id"]})
    product = store.get("products", purchase["product_id"])
    summary = f"{product['name']} {_variant_label(product, purchase['variant_id'])}, approved charge {fmt_cents(purchase['approved_charge_cents'])}"
    issue, interp = parse_complaint(complaint_text, summary)
    claim_id = next_id(store, "clm")
    cls = classify(store, purchase, issue, exclude_claim_id=claim_id)
    claim = {
        "_id": claim_id, "claim_id": claim_id, "purchase_id": purchase_id, "shopper_id": shopper_id, "merchant_id": MERCHANT_ID,
        "complaint_text": complaint_text, "clarifications": [], "structured_issue": issue, "interpreter": interp,
        **cls, "evidence_status": cls.get("evidence_status", "customer_report_only"),
        "status": "needs_info" if cls["classification"] == "NEEDS_REVIEW" and cls["follow_up_question"] else "open",
        "accepted_remedy_id": None, "validated_outcome": None, "created_at": now(store).isoformat(), "source": "live",
        "history": [{"at": now(store).isoformat(), "status": "created", "classification": cls["classification"]}],
    }
    store.put("claims", claim)
    append_event(store, subject_type="claim", subject_id=claim_id, purchase_id=purchase_id, event_type="claim.created",
                 actor_type="shopper", payload={"problem_type": issue["problem_type"], "interpreter": interp["mode"]})
    append_event(store, subject_type="claim", subject_id=claim_id, purchase_id=purchase_id, event_type="claim.classified",
                 actor_type="classifier", payload={"classification": cls["classification"], "requires_review": cls["requires_review"]})
    generate_remedies(store, claim_id)
    return store.get("claims", claim_id)


def clarify_claim(store: BaseStore, claim_id: str, text: str) -> dict:
    claim = _claim(store, claim_id)
    if claim["status"] not in ("needs_info", "open") or claim["accepted_remedy_id"]:
        raise DomainError("This claim can no longer be changed.", 409, "invalid_state")
    purchase = _purchase(store, claim["purchase_id"])
    product = store.get("products", purchase["product_id"])
    summary = f"{product['name']} {_variant_label(product, purchase['variant_id'])}"
    issue, interp = parse_complaint(f"{claim['complaint_text']}\nFollow-up answer: {text}", summary)
    # Prefer the follow-up answer when it is specific.
    follow_issue, _ = parse_complaint(text, summary)
    if follow_issue["problem_type"] != "unclear":
        issue = follow_issue
    cls = classify(store, purchase, issue, exclude_claim_id=claim_id)
    claim["clarifications"].append({"at": now(store).isoformat(), "text": text})
    claim.update(structured_issue=issue, interpreter=interp, **cls)
    claim["evidence_status"] = cls.get("evidence_status", "customer_report_only")
    claim["status"] = "needs_info" if cls["classification"] == "NEEDS_REVIEW" and cls["follow_up_question"] else "open"
    claim["history"].append({"at": now(store).isoformat(), "status": "clarified", "classification": cls["classification"]})
    store.put("claims", claim)
    append_event(store, subject_type="claim", subject_id=claim_id, purchase_id=claim["purchase_id"], event_type="claim.classified",
                 actor_type="classifier", payload={"classification": cls["classification"], "after_clarification": True})
    for r in store.find("remedies", {"claim_id": claim_id}):
        store.delete("remedies", r["_id"])
    generate_remedies(store, claim_id)
    return store.get("claims", claim_id)


# ---------------------------------------------------------------- remedies


def verify_exchange(store: BaseStore, purchase: dict, target_variant_id: str) -> dict:
    """An exchange goes back through product, inventory and price verification."""
    product = store.get("products", purchase["product_id"])
    target = find_variant(product, target_variant_id)
    checks = []
    if not target:
        checks.append(_check("VARIANT_EXISTS", "Replacement variant", FAIL, target_variant_id, "valid variant", "Variant does not exist."))
        return {"status": FAIL, "checks": checks}
    checks.append(_check("SAME_PRODUCT", "Same product", PASS, product["name"], purchase["product_name"], "Exchange keeps the same product."))
    stock = product["inventory_by_variant"].get(target_variant_id, 0)
    checks.append(_check("STOCK", "Replacement in stock", PASS if stock >= purchase["quantity"] else FAIL, f"{stock} available",
                         f"{purchase['quantity']} needed", "Reserved on confirmation." if stock else "Out of stock."))
    paid_unit = purchase["full_price_breakdown"]["unit_price_cents"]
    ok = product["price_cents"] <= paid_unit
    checks.append(_check("PRICE_NEUTRAL", "No additional charge", PASS if ok else FAIL, fmt_cents(product["price_cents"]),
                         f"≤ {fmt_cents(paid_unit)} paid", "Even exchange; nothing new is charged." if ok else "Replacement costs more than the original."))
    # Store policy: replacements ship express at no cost to the shopper.
    ship = next((o for o in product["delivery_options"] if o["id"] == "express"), product["delivery_options"][0])
    est = estimate_delivery(store, ship)
    checks.append(_check("REPLACEMENT_DELIVERY", "Replacement delivery estimate", INFO, est.isoformat(), "best effort",
                         f"Replacement ships {ship['label'].lower()} at no cost.", blocking=False))
    return {"status": FAIL if any(c["status"] == FAIL for c in checks) else PASS, "checks": checks,
            "estimated_delivery_date": est.isoformat(), "target_label": _variant_label(product, target_variant_id)}



def _merchant_value(policy: dict, remedy_type: str, cost_cents: int) -> int:
    a = policy["remedy_value_assumptions"]
    p = a["repeat_purchase_probability"].get(remedy_type, 0.3)
    return round(a["future_contribution_margin_cents"] * p - cost_cents)


def generate_remedies(store: BaseStore, claim_id: str) -> list[dict]:
    claim = _claim(store, claim_id)
    existing = store.find("remedies", {"claim_id": claim_id})
    if existing:
        return sorted(existing, key=lambda r: r["rank"])
    purchase = _purchase(store, claim["purchase_id"])
    product = store.get("products", purchase["product_id"])
    policy = store.get("policies", "pol_stridelab")
    a = policy["remedy_value_assumptions"]
    sh = shipment_for(store, purchase["purchase_id"])
    charge = purchase["approved_charge_cents"]
    pm_label = purchase["payment_method_reference"]["label"]
    cls = claim["classification"]
    issue = claim["structured_issue"]
    in_window = True
    if sh and sh.get("delivered_at"):
        in_window = now(store) <= datetime.fromisoformat(sh["delivered_at"]) + timedelta(days=policy["return_window_days"])
    drafts: list[dict] = []

    def add(rtype, title, description, amount=0, cost=0, eligible=True, reason=None, extra=None):
        drafts.append({"remedy_type": rtype, "title": title, "customer_facing_description": description, "amount_cents": amount,
                       "merchant_cost_cents": cost, "eligible": eligible, "eligibility_reason": reason, **(extra or {})})

    def exchange_options(sizes: list[str], colors: list[str], limit=3, allow_same=False):
        seen = 0
        for color in colors:
            for size in sizes:
                v = next((x for x in product["variants"] if x["color"] == color and x["size"] == size), None)
                if not v or (v["variant_id"] == purchase["variant_id"] and not allow_same):
                    continue
                ver = verify_exchange(store, purchase, v["variant_id"])
                ok = ver["status"] == PASS
                add("exchange", f"Exchange for {color.title()} size {size}",
                    f"Swap for {color} US {size}. Free return label; replacement ships at no charge"
                    f" (est. {ver.get('estimated_delivery_date')}).",
                    0, a["return_label_cost_cents"] + a["outbound_shipping_cost_cents"], ok,
                    None if ok else "; ".join(c["explanation"] for c in ver["checks"] if c["status"] == FAIL),
                    {"target_variant_id": v["variant_id"], "exchange_verification": ver})
                seen += 1
                if seen >= limit:
                    return

    if not in_window:
        add("manual_review", "Ask the merchant to review", "This order is outside the 30-day return window. A person will review your request.", cost=5_00)
    elif cls == "SUSPECTED_PRODUCT_FIT":
        sizes = sorted({v["size"] for v in product["variants"]}, key=float)
        cur = float(purchase["size"])
        if issue["problem_type"] == "fit_tight":
            order = [s for s in sizes if float(s) > cur]
        else:
            order = [s for s in sorted(sizes, key=float, reverse=True) if float(s) < cur]
        if issue.get("mentioned_size") in order:
            order.remove(issue["mentioned_size"])
            order.insert(0, issue["mentioned_size"])
        exchange_options(order[:2], [purchase["color"]], limit=2)
        add("refund", "Return for a full refund", f"Refund {fmt_cents(charge)} to {pm_label} after the return is received (simulated).",
            charge, charge + a["return_label_cost_cents"])
        if policy["allow_store_credit"]:
            add("store_credit", "Store credit instead", f"{fmt_cents(charge)} StrideLab store credit, same value as the refund. Only if you prefer it.",
                charge, charge)
    elif cls == "ORDER_MISMATCH":
        exchange_options([purchase["size"]], [purchase["color"]], limit=1, allow_same=True)
        for d in drafts:
            if d["remedy_type"] == "exchange":
                d["title"] = "Send the item you actually ordered"
        add("refund", "Return for a full refund", f"Refund {fmt_cents(charge)} to {pm_label} (simulated). Free return label.", charge,
            charge + a["return_label_cost_cents"])
        pct = policy["keep_item_partial_refund_pct"]
        keep = round(purchase["full_price_breakdown"]["merchandise_cents"] * pct / 100)
        add("keep_item_partial_refund", f"Keep it with a {pct}% refund", f"Keep the delivered pair and get {fmt_cents(keep)} back to {pm_label} (simulated).",
            keep, keep)
    elif cls == "PRICE_OR_DELIVERY_MISMATCH" and claim.get("mismatch_kind") == "price":
        over = claim.get("overcharge_cents", 0)
        add("partial_refund", f"Refund the {fmt_cents(over)} overcharge", f"Return {fmt_cents(over)} to {pm_label} so you pay exactly what you approved (simulated).",
            over, over)
    elif cls == "PRICE_OR_DELIVERY_MISMATCH":
        ship_cost = purchase["full_price_breakdown"]["shipping_cents"]
        if ship_cost:
            add("partial_refund", f"Refund the {fmt_cents(ship_cost)} shipping charge", f"Keep your order and get the shipping back to {pm_label} (simulated).",
                ship_cost, ship_cost)
        add("refund", "Return for a full refund", f"Refund {fmt_cents(charge)} to {pm_label} after the return (simulated).", charge,
            charge + a["return_label_cost_cents"])
    elif cls == "CUSTOMER_PREFERENCE":
        colors = [issue["mentioned_color"]] if issue.get("mentioned_color") else []
        colors += [c for c in dict.fromkeys(v["color"] for v in product["variants"]) if c not in colors]
        sizes = [issue["mentioned_size"]] if issue.get("mentioned_size") else []
        sizes += [purchase["size"]] + [s for s in dict.fromkeys(v["size"] for v in product["variants"]) if s not in sizes and s != purchase["size"]]
        exchange_options(sizes, colors, limit=2)
        fee = policy["preference_return_fee_cents"]
        add("refund", "Return for a refund", f"Refund {fmt_cents(charge - fee)} to {pm_label} ({fmt_cents(fee)} return-shipping fee per store policy; simulated).",
            charge - fee, charge - fee)
        if policy["allow_store_credit"]:
            add("store_credit", "Store credit instead", f"{fmt_cents(charge - fee)} store credit, same value as the refund. Only if you prefer it.",
                charge - fee, charge - fee)
    add("manual_review", "Ask a person to review", "A StrideLab team member reviews your case and replies. Nothing is decided automatically.",
        cost=5_00)
    if cls == "NEEDS_REVIEW":
        drafts = [d for d in drafts if d["remedy_type"] == "manual_review"]

    # Rank: eligible first, shopper's requested outcome next, then transparent merchant value assumptions.
    requested = issue.get("requested_outcome")
    for d in drafts:
        d["merchant_value_score_cents"] = _merchant_value(policy, d["remedy_type"], d["merchant_cost_cents"])
    # Store credit never outranks an equivalent cash refund (no nudging toward credit).
    drafts.sort(key=lambda d: (not d["eligible"], d["remedy_type"] == "manual_review",
                               d["remedy_type"] == "store_credit" and requested != "store_credit",
                               d["remedy_type"] != requested, -d["merchant_value_score_cents"]))
    out = []
    for i, d in enumerate(drafts):
        rid = next_id(store, "rem")
        doc = {"_id": rid, "remedy_id": rid, "claim_id": claim_id, "purchase_id": purchase["purchase_id"], "rank": i,
               "eligibility_result": {"eligible": d.pop("eligible"), "reason": d.pop("eligibility_reason")},
               "status": "eligible", "customer_selected": False, "simulated_financial_event_id": None, **d}
        if not doc["eligibility_result"]["eligible"]:
            doc["status"] = "ineligible"
        store.put("remedies", doc)
        out.append(doc)
    append_event(store, subject_type="claim", subject_id=claim_id, purchase_id=purchase["purchase_id"], event_type="remedies.presented",
                 actor_type="remedy_engine", payload={"eligible": [r["remedy_type"] for r in out if r["status"] == "eligible"]})
    return out


def accept_remedy(store: BaseStore, remedy_id: str, shopper_id: str, confirm: bool, idempotency_key: str | None,
                  payout_method: str) -> dict:
    remedy = store.get("remedies", remedy_id)
    if not remedy:
        raise DomainError(f"Unknown remedy '{remedy_id}'.", 404, "not_found")
    claim = _claim(store, remedy["claim_id"])
    if claim["shopper_id"] != shopper_id:
        raise DomainError("This claim belongs to a different shopper.", 403, "forbidden")
    if claim["accepted_remedy_id"]:
        if claim["accepted_remedy_id"] == remedy_id:
            return {"duplicate": True, "remedy": remedy, "claim": claim,
                    "message": "Already processed. No second refund or exchange was issued."}
        raise DomainError("A different remedy was already accepted for this claim.", 409, "remedy_already_accepted")
    if not confirm:
        raise DomainError("Please explicitly confirm the remedy.", 400, "approval_required")
    if remedy["status"] != "eligible":
        raise DomainError("That option isn't eligible for this claim.", 409, "ineligible")
    purchase = _purchase(store, claim["purchase_id"])
    product = store.get("products", purchase["product_id"])
    rtype = remedy["remedy_type"]
    execution: dict = {"idempotency_key": idempotency_key}

    if rtype == "exchange":
        ver = verify_exchange(store, purchase, remedy["target_variant_id"])
        if ver["status"] != PASS:
            raise DomainError("The replacement is no longer available: " + "; ".join(c["explanation"] for c in ver["checks"] if c["status"] == FAIL),
                              409, "inventory_conflict", {"verification": ver})
        product["inventory_by_variant"][remedy["target_variant_id"]] -= purchase["quantity"]
        store.put("products", product)
        target = find_variant(product, remedy["target_variant_id"])
        ex_id = next_id(store, "pur")
        bd = {**purchase["full_price_breakdown"], "charge_cents": 0, "shipping_cents": 0, "tax_cents": 0, "coupon_discount_cents": 0,
              "merchandise_cents": 0, "later_statement_credit_cents": 0, "effective_cost_cents": 0, "note": "Even exchange — no charge.",
              "coupon_evaluations": [], "card_offer_evaluations": []}
        est = date.fromisoformat(ver["estimated_delivery_date"])
        ex = make_purchase_record(store, purchase_id=ex_id, shopper_id=purchase["shopper_id"], product=product, variant=target,
                                  quantity=purchase["quantity"], breakdown=bd, payment_method_id=purchase["payment_method_reference"]["id"],
                                  approved_delivery_date=est, verification_checks=ver["checks"], simulated_transaction_id="none (no charge)",
                                  created_at=now(store), kind="exchange", parent_purchase_id=purchase["purchase_id"])
        ex["claim_id"] = claim["claim_id"]
        store.put("purchases", ex)
        append_event(store, subject_type="purchase", subject_id=ex_id, purchase_id=ex_id, event_type="exchange_order.created",
                     actor_type="system", payload={"parent_purchase_id": purchase["purchase_id"], "variant_id": target["variant_id"],
                                                   "inventory_reserved": True})
        make_shipment(store, shipment_id=next_id(store, "shp"), purchase_id=ex_id, variant_id=target["variant_id"],
                      placed_at=now(store), expected=est)
        process_shipments(store)
        execution.update(exchange_purchase_id=ex_id, replacement_variant_id=target["variant_id"], return_label="SIM-RTN-" + ex_id[-4:])
        purchase["status"] = "exchange_in_progress"
        claim["status"] = "exchange_in_progress"
        fin_id = None
    elif rtype in ("refund", "partial_refund", "keep_item_partial_refund", "store_credit"):
        prefix = "SIM-CRD" if rtype == "store_credit" else "SIM-RFD"
        fin_id = next_id(store, prefix)
        method = "store_credit" if rtype == "store_credit" else payout_method
        execution.update(amount_cents=remedy["amount_cents"], method=method, simulated_transaction_id=fin_id,
                         settlement_note=("Simulated. Real card refunds commonly take several business days to appear."
                                          if method == "original_payment_method" else
                                          "Concept only: an eligible push-to-card payout could reach a card faster than a refund; "
                                          "eligibility and timing depend on the issuer and program. Simulated here."
                                          if method == "push_to_card_concept" else "Simulated store credit balance."))
        purchase["status"] = {"refund": "refunded", "store_credit": "returned_for_credit"}.get(rtype, "partially_refunded")
        claim["status"] = "resolved"
        append_event(store, subject_type="purchase", subject_id=purchase["purchase_id"], purchase_id=purchase["purchase_id"],
                     event_type="refund.simulated" if rtype != "store_credit" else "store_credit.simulated", actor_type="payment_simulator",
                     payload={"amount_cents": remedy["amount_cents"], "method": method, "transaction_id": fin_id, "simulated": True,
                              "claim_id": claim["claim_id"]})
    else:  # manual_review
        fin_id = None
        claim["status"] = "in_review"
        claim["requires_review"] = True

    remedy.update(status="accepted", customer_selected=True, simulated_financial_event_id=fin_id, execution=execution,
                  accepted_at=now(store).isoformat())
    store.put("remedies", remedy)
    for other in store.find("remedies", {"claim_id": claim["claim_id"]}):
        if other["remedy_id"] != remedy_id and other["status"] == "eligible":
            other["status"] = "not_selected"
            store.put("remedies", other)
    claim["accepted_remedy_id"] = remedy_id
    claim["history"].append({"at": now(store).isoformat(), "status": claim["status"], "remedy": rtype})
    store.put("claims", claim)
    store.put("purchases", purchase)
    append_event(store, subject_type="claim", subject_id=claim["claim_id"], purchase_id=purchase["purchase_id"], event_type="remedy.executed",
                 actor_type="shopper", payload={"remedy_id": remedy_id, "remedy_type": rtype, "simulated": True,
                                                "amount_cents": remedy["amount_cents"], "financial_event_id": fin_id})
    return {"duplicate": False, "remedy": remedy, "claim": claim}


def exchange_status(store: BaseStore, claim: dict) -> dict | None:
    if not claim.get("accepted_remedy_id"):
        return None
    remedy = store.get("remedies", claim["accepted_remedy_id"])
    if remedy["remedy_type"] != "exchange":
        return None
    ex_id = remedy["execution"]["exchange_purchase_id"]
    sh = shipment_for(store, ex_id)
    return {"exchange_purchase_id": ex_id, "shipment": sh, "delivered": bool(sh and sh.get("delivered_at")),
            "replacement_variant_id": remedy["execution"]["replacement_variant_id"]}


def confirm_outcome(store: BaseStore, claim_id: str, shopper_id: str, outcome: str) -> dict:
    claim = _claim(store, claim_id)
    if claim["shopper_id"] != shopper_id:
        raise DomainError("This claim belongs to a different shopper.", 403, "forbidden")
    ex = exchange_status(store, claim)
    if not ex:
        raise DomainError("Only exchanges ask for a fit confirmation.", 409, "invalid_state")
    if not ex["delivered"]:
        raise DomainError("The replacement hasn't been delivered yet.", 409, "not_delivered")
    if claim.get("validated_outcome") or claim["status"] == "resolved":
        return {"claim": claim, "rule_proposal": None, "duplicate": True}
    purchase = _purchase(store, claim["purchase_id"])
    product = store.get("products", purchase["product_id"])
    replacement = find_variant(product, ex["replacement_variant_id"])
    proposal = None
    if outcome == "fits":
        claim["status"] = "resolved"
        purchase["status"] = "exchanged"
        if claim["classification"] == "SUSPECTED_PRODUCT_FIT":
            claim["validated_outcome"] = {"type": "fit_exchange_confirmed", "product_id": purchase["product_id"],
                                          "from_size": purchase["size"], "to_size": replacement["size"],
                                          "replacement_variant_id": replacement["variant_id"], "confirmed_at": now(store).isoformat()}
            claim["evidence_status"] = "confirmed_outcome"
    else:
        claim["status"] = "in_review"
        claim["requires_review"] = True
    claim["history"].append({"at": now(store).isoformat(), "status": claim["status"], "outcome": outcome})
    store.put("claims", claim)
    store.put("purchases", purchase)
    append_event(store, subject_type="claim", subject_id=claim_id, purchase_id=purchase["purchase_id"], event_type="claim.outcome_confirmed",
                 actor_type="shopper", payload={"outcome": outcome, "validated": bool(claim.get("validated_outcome"))})
    if claim.get("validated_outcome"):
        proposal = evaluate_group(store, purchase["product_id"], purchase["size"])
    return {"claim": claim, "rule_proposal": proposal, "duplicate": False}


def claim_view(store: BaseStore, claim_id: str) -> dict:
    claim = _claim(store, claim_id)
    purchase = _purchase(store, claim["purchase_id"])
    remedies = sorted(store.find("remedies", {"claim_id": claim_id}), key=lambda r: r["rank"])
    events = sorted([e for e in store.find("events", {"subject_id": claim_id})], key=lambda e: e["seq"])
    return {"claim": claim, "purchase": purchase, "remedies": remedies, "exchange": exchange_status(store, claim), "events": events}

