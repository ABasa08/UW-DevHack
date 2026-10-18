"""Return-to-Rule learning.

Aggregates *validated* outcomes (a fit exchange whose replacement the shopper confirmed
fits) per product and size. When illustrative demo thresholds are met it drafts a
proposed rule. Proposed rules never affect shoppers until a merchant approves them, and
they only ever *suggest* a size; they never change a shopper's selection.
"""

from __future__ import annotations

from collections import Counter

from .ai import draft_rule_text
from .core import POLICY_ID, DomainError, MERCHANT_ID, append_event, next_id, now, shopify_mode
from .pricing import sizes_in_order
from .store import BaseStore


def group_stats(store: BaseStore, product_id: str, size: str, exclude_claim_id: str | None = None) -> dict:
    fulfilled = []
    for p in store.find("purchases", {"product_id": product_id, "kind": "order"}):
        if p["size"] != size:
            continue
        sh = store.find_one("shipments", {"purchase_id": p["purchase_id"]})
        if sh and sh.get("delivered_at"):
            fulfilled.append(p["purchase_id"])
    # Fulfilled Shopify orders placed without PerkPilot also belong to the comparison group (never double-counted).
    product = store.get("products", product_id) or {}
    sized = {v["variant_id"] for v in product.get("variants", []) if v["size"] == size}
    imported = 0
    for o in store.find("shopify_orders"):
        if o.get("perkpilot_purchase_id") or o["cancelled"] or o["fulfillment_status"] != "fulfilled":
            continue
        imported += sum(i["quantity"] for i in o["items"] if i.get("variant_id") in sized)
    validated, reported_only = [], []
    for c in store.find("claims", {"classification": "SUSPECTED_PRODUCT_FIT"}):
        if c["claim_id"] == exclude_claim_id:
            continue
        p = store.get("purchases", c["purchase_id"])
        if not p or p["product_id"] != product_id or p["size"] != size:
            continue
        vo = c.get("validated_outcome")
        if vo and vo.get("type") == "fit_exchange_confirmed":
            validated.append(c)
        elif c["structured_issue"]["problem_type"] == "fit_tight":
            reported_only.append(c["claim_id"])
    return {
        "product_id": product_id, "size": size,
        "fulfilled_order_count": len(fulfilled) + imported,
        "imported_shopify_orders": imported,
        "validated_outcome_count": len(validated),
        "validated_claim_ids": [c["claim_id"] for c in validated],
        "seeded_outcome_count": sum(1 for c in validated if c.get("source") == "seed"),
        "validated_to_sizes": [c["validated_outcome"]["to_size"] for c in validated],
        "unvalidated_report_ids": reported_only,
    }


def evaluate_group(store: BaseStore, product_id: str, size: str) -> dict | None:
    policy = store.get("policies", POLICY_ID)
    th = policy["learning_thresholds"]
    stats = group_stats(store, product_id, size)
    existing = [r for r in store.find("rules", {"product_scope": product_id}) if r["variant_scope"].get("size") == size
                and r["status"] in ("proposed", "approved")]
    if existing:
        rule = existing[0]
        if rule["status"] == "proposed":
            rule.update(affected_order_count=stats["fulfilled_order_count"], confirmed_outcome_count=stats["validated_outcome_count"],
                        supporting_claim_ids=stats["validated_claim_ids"])
            store.put("rules", rule)
        return rule
    if stats["fulfilled_order_count"] < th["min_fulfilled_orders"] or stats["validated_outcome_count"] < th["min_validated_outcomes"]:
        return None
    to_size, n = Counter(stats["validated_to_sizes"]).most_common(1)[0]
    if n * 3 < stats["validated_outcome_count"] * 2:  # need a consistent direction (>= 2/3 agree)
        return None
    product = store.get("products", product_id)
    targets = [v for v in product["variants"] if v["size"] == to_size]
    if not targets or not any(product["inventory_by_variant"].get(v["variant_id"], 0) > 0 for v in targets):
        return None  # would contradict catalog/inventory
    facts = {"product_name": product["name"], "from_size": size, "to_size": to_size,
             "validated": stats["validated_outcome_count"], "fulfilled": stats["fulfilled_order_count"]}
    wording, wording_meta = draft_rule_text(facts)
    rule_id = next_id(store, "rule")
    rate = stats["validated_outcome_count"] / stats["fulfilled_order_count"]
    rule = {
        "_id": rule_id, "rule_id": rule_id, "merchant_id": MERCHANT_ID, "product_scope": product_id, "product_name": product["name"],
        "variant_scope": {"size": size, "colors": "all"}, "issue_type": "fit_tight",
        "supporting_claim_ids": stats["validated_claim_ids"], "affected_order_count": stats["fulfilled_order_count"],
        "confirmed_outcome_count": stats["validated_outcome_count"], "unvalidated_report_count": len(stats["unvalidated_report_ids"]),
        "observed_rate": round(rate, 3),
        "proposed_condition": {"product_id": product_id, "selected_size": size},
        "proposed_action": {"type": "suggest_size_review", "suggest_size": to_size, "auto_change_selection": False},
        "customer_message": wording["customer_message"], "merchant_summary": wording["merchant_summary"], "wording_source": wording_meta,
        "limitations": [
            f"Small sample: {stats['validated_outcome_count']} confirmed outcomes among {stats['fulfilled_order_count']} fulfilled orders "
            f"({rate:.0%}). Not statistically validated.",
            f"{len(stats['unvalidated_report_ids'])} other tight-fit report(s) were excluded because the outcome was never confirmed.",
            (f"{stats['seeded_outcome_count']} of the confirmed outcomes are labeled synthetic sample history. " if stats["seeded_outcome_count"]
             else "") + "Indicates a possible fit pattern, not a proven product defect.",
            "Guidance is shown to shoppers as a suggestion; their selected size is never changed automatically.",
        ],
        "thresholds": th,
        "approval_actor": None, "approval_timestamp": None, "version": 1, "status": "proposed", "enabled": False,
        "created_at": now(store).isoformat(),
        "history": [{"at": now(store).isoformat(), "action": "proposed", "actor": "learning_engine"}],
    }
    store.put("rules", rule)
    append_event(store, subject_type="rule", subject_id=rule_id, event_type="rule.proposed", actor_type="learning_engine",
                 payload={"product_id": product_id, "size": size, "to_size": to_size, "validated": stats["validated_outcome_count"],
                          "fulfilled": stats["fulfilled_order_count"]})
    return rule


def _publish(store: BaseStore, rule: dict) -> dict:
    """Mirror approved guidance to the Shopify product page (metafield). A failed write is recorded, not raised:
    the rule's state in PerkPilot is the source of truth and the next change retries."""
    if not shopify_mode():
        return rule
    from .shopify_commerce import sync_guidance

    rule["storefront_sync"] = sync_guidance(store, rule["product_scope"])
    store.put("rules", rule)
    return rule


def _rule(store: BaseStore, rule_id: str) -> dict:
    r = store.get("rules", rule_id)
    if not r or r["merchant_id"] != MERCHANT_ID:
        raise DomainError(f"Unknown rule '{rule_id}'.", 404, "not_found")
    return r


def approve_rule(store: BaseStore, rule_id: str, actor: str, note: str | None) -> dict:
    r = _rule(store, rule_id)
    if r["status"] == "approved":
        return r
    if r["status"] != "proposed":
        raise DomainError(f"Only proposed rules can be approved (this one is {r['status']}).", 409, "invalid_state")
    r.update(status="approved", enabled=True, approval_actor=actor, approval_timestamp=now(store).isoformat())
    r["history"].append({"at": r["approval_timestamp"], "action": "approved", "actor": actor, "note": note})
    store.put("rules", r)
    append_event(store, subject_type="rule", subject_id=rule_id, event_type="rule.approved", actor_type="merchant", payload={"actor": actor})
    return _publish(store, r)


def reject_rule(store: BaseStore, rule_id: str, actor: str, note: str | None) -> dict:
    r = _rule(store, rule_id)
    if r["status"] != "proposed":
        raise DomainError("Only proposed rules can be rejected.", 409, "invalid_state")
    r["status"] = "rejected"
    r["history"].append({"at": now(store).isoformat(), "action": "rejected", "actor": actor, "note": note})
    store.put("rules", r)
    append_event(store, subject_type="rule", subject_id=rule_id, event_type="rule.rejected", actor_type="merchant", payload={"actor": actor})
    return r


def toggle_rule(store: BaseStore, rule_id: str, enabled: bool, actor: str) -> dict:
    r = _rule(store, rule_id)
    if r["status"] != "approved":
        raise DomainError("Only approved rules can be paused or resumed.", 409, "invalid_state")
    if r["enabled"] != enabled:
        r["enabled"] = enabled
        r["version"] += 1
        r["history"].append({"at": now(store).isoformat(), "action": "resumed" if enabled else "paused", "actor": actor})
        store.put("rules", r)
        append_event(store, subject_type="rule", subject_id=rule_id, event_type="rule.resumed" if enabled else "rule.paused",
                     actor_type="merchant", payload={"actor": actor})
        _publish(store, r)
    return r


def rules_overview(store: BaseStore) -> dict:
    rules = sorted(store.find("rules", {"merchant_id": MERCHANT_ID}), key=lambda r: r["created_at"], reverse=True)
    for r in rules:
        r["evidence"] = []
        for cid in r["supporting_claim_ids"]:
            c = store.get("claims", cid)
            if c:
                r["evidence"].append({"claim_id": cid, "purchase_id": c["purchase_id"], "source": c.get("source", "live"),
                                      "complaint": c["complaint_text"], "outcome": c["validated_outcome"]})
    watch = []
    for p in store.find("products", {"status": "active"}):
        for size in sizes_in_order(p):
            s = group_stats(store, p["product_id"], size)
            if s["validated_outcome_count"] or s["unvalidated_report_ids"]:
                watch.append({**s, "product_name": p["name"]})
    return {"rules": rules, "watchlist": watch, "thresholds": store.get("policies", POLICY_ID)["learning_thresholds"]}
