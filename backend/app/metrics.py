"""Merchant dashboard metrics, computed only from persisted events and records."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from .core import MERCHANT_ID, fmt_cents
from .store import BaseStore

REFUND_TYPES = {"refund", "partial_refund", "keep_item_partial_refund"}


def dashboard(store: BaseStore) -> dict:
    events = sorted(store.find("events", {"merchant_id": MERCHANT_ID}), key=lambda e: e["seq"])
    live = [e for e in events if e["source"] == "live"]

    def of(etype, evs=live):
        return [e for e in evs if e["event_type"] == etype]

    verifications = of("checkout.verified")
    blocked = [e for e in verifications if e["payload"]["status"] == "FAIL"]
    passed = [e for e in verifications if e["payload"]["status"] == "PASS"]
    confirmed = of("purchase.confirmed")
    executed = of("remedy.executed")
    exchanges = [e for e in executed if e["payload"]["remedy_type"] == "exchange"]
    refunds = [e for e in executed if e["payload"]["remedy_type"] in REFUND_TYPES]
    credits = [e for e in executed if e["payload"]["remedy_type"] == "store_credit"]
    reconfirm = of("checkout.reconfirmation_required") + of("checkout.blocked_at_confirm")
    handed_off = of("checkout.handed_off")
    awaiting = [r for r in store.find("remedies") if r["status"] == "awaiting_merchant"]

    # complaint -> remedy presentation latency (wall clock of the running app)
    latencies = []
    created = {e["subject_id"]: e for e in of("claim.created")}
    for e in of("remedies.presented"):
        c = created.get(e["subject_id"])
        if c:
            ms = (datetime.fromisoformat(e["wall_time"]) - datetime.fromisoformat(c["wall_time"])).total_seconds() * 1000
            latencies.append(ms)

    claims = store.find("claims", {"merchant_id": MERCHANT_ID})
    live_claims = [c for c in claims if c.get("source") == "live"]
    dist_all = Counter(c["classification"] for c in claims)
    dist_live = Counter(c["classification"] for c in live_claims)
    rules = store.find("rules", {"merchant_id": MERCHANT_ID})

    retained = 0
    for e in exchanges:
        p = store.get("purchases", e["purchase_id"])
        if p:
            retained += p["approved_charge_cents"]

    failed_rules = Counter(r for e in blocked for r in e["payload"]["failed_rules"])
    purchases = [p for p in store.find("purchases", {"merchant_id": MERCHANT_ID}) if p.get("source") == "live" and p["kind"] == "order"]
    feed = [
        {"event_id": e["event_id"], "event_type": e["event_type"], "timestamp": e["timestamp"], "subject_id": e["subject_id"],
         "actor_type": e["actor_type"], "payload": e["payload"]}
        for e in reversed(live[-25:])
    ]
    runs = sorted(store.find("eval_runs"), key=lambda r: r["timestamp"], reverse=True)
    return {
        "session_metrics": {
            "proposals_verified": len(verifications),
            "proposals_blocked": len(blocked),
            "proposals_passed": len(passed),
            "orders_confirmed": len(confirmed),
            "checkouts_handed_off": len(handed_off),
            "remedies_awaiting_merchant": len(awaiting),
            "post_approval_changes_caught": len(reconfirm),
            "claims_filed": len(live_claims),
            "claims_needing_review": sum(1 for c in live_claims if c["classification"] == "NEEDS_REVIEW"),
            "exchanges_completed": len(exchanges),
            "refunds_completed": len(refunds),
            "refund_amount_cents": sum(e["payload"].get("amount_cents", 0) for e in refunds),
            "store_credits_issued": len(credits),
            "rules_proposed": sum(1 for r in rules if r["status"] in ("proposed", "approved", "rejected")),
            "rules_approved": sum(1 for r in rules if r["status"] == "approved"),
            "rules_active": sum(1 for r in rules if r["status"] == "approved" and r.get("enabled")),
            "avg_complaint_to_remedy_ms": round(sum(latencies) / len(latencies)) if latencies else None,
            "order_value_kept_via_exchange_cents": retained,
        },
        "labels": {
            "session_metrics": "Counted from events persisted by this PerkPilot install (seeded and sample history excluded).",
            "order_value_kept_via_exchange_cents": ("Approved charge of orders resolved by exchange instead of refund. On Shopify test orders "
                                                    "no real money moved; either way it's a proxy, not measured revenue."),
            "avg_complaint_to_remedy_ms": "Wall-clock time from complaint submission to eligible remedies being presented, in this app.",
        },
        "blocked_by_rule": dict(failed_rules),
        "issue_distribution": {"all": dict(dist_all), "live": dict(dist_live),
                               "note": "'all' includes seeded historical claims (synthetic)."},
        "recent_purchases": sorted([
            {"purchase_id": p["purchase_id"], "shopper_id": p["shopper_id"], "product_name": p["product_name"], "size": p["size"],
             "shopify_order_name": p.get("shopify_order_name"), "shopify_test": p.get("shopify_test"),
             "color": p["color"], "approved_charge": fmt_cents(p["approved_charge_cents"]), "status": p["status"],
             "checks_passed": sum(1 for c in p["verification_results"] if c["status"] == "PASS"),
             "checks_total": len(p["verification_results"]), "created_at": p["created_at"]}
            for p in purchases], key=lambda x: x["created_at"], reverse=True),
        "seeded_history": {
            "orders": sum(1 for p in store.find("purchases") if p.get("source") == "seed"),
            "claims": sum(1 for c in claims if c.get("source") == "seed"),
            "note": "Synthetic fixtures used to seed the learning comparison group and edge-case scenarios.",
        },
        "activity": feed,
        "latest_evaluation": runs[0] if runs else None,
    }
