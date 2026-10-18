"""Verified Purchase Records and simulated shipments."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .core import MERCHANT_ID, STORE_TZ, add_business_days, append_event, now, sha256_hex, verify_event_chain
from .store import BaseStore

HASHED_FIELDS = [
    "purchase_id", "kind", "parent_purchase_id", "intent_id", "merchant_id", "shopper_id", "original_request",
    "confirmed_requirements", "confirmation_version", "confirmation_time", "product_id", "variant_id", "sku",
    "quantity", "full_price_breakdown", "payment_method_reference", "approved_charge_cents", "currency",
    "approved_delivery_date", "verification_results", "authorization_event_id", "simulated_transaction_id",
    "record_version", "created_at",
]


def record_hash(record: dict) -> str:
    return sha256_hex({k: record.get(k) for k in HASHED_FIELDS})


def make_purchase_record(
    store: BaseStore,
    *,
    purchase_id: str,
    shopper_id: str,
    product: dict,
    variant: dict,
    quantity: int,
    breakdown: dict,
    payment_method_id: str,
    approved_delivery_date: date,
    verification_checks: list[dict],
    simulated_transaction_id: str,
    created_at: datetime,
    intent: dict | None = None,
    authorization_event_id: str | None = None,
    kind: str = "order",
    parent_purchase_id: str | None = None,
    source: str = "live",
) -> dict:
    pm = store.get("payment_methods", payment_method_id)
    record = {
        "_id": purchase_id,
        "purchase_id": purchase_id,
        "kind": kind,
        "parent_purchase_id": parent_purchase_id,
        "intent_id": intent["intent_id"] if intent else None,
        "merchant_id": MERCHANT_ID,
        "shopper_id": shopper_id,
        "original_request": intent["original_request"] if intent else None,
        "confirmed_requirements": intent["confirmed_requirements"] if intent else None,
        "confirmation_version": intent["confirmation_version"] if intent else None,
        "confirmation_time": intent["confirmation_time"] if intent else None,
        "product_id": product["product_id"],
        "product_name": product["name"],
        "variant_id": variant["variant_id"],
        "sku": variant["sku"],
        "color": variant["color"],
        "size": variant["size"],
        "quantity": quantity,
        "full_price_breakdown": breakdown,
        "payment_method_reference": {"id": payment_method_id, "label": pm["label"] if pm else payment_method_id, "simulated": True},
        "approved_charge_cents": breakdown["charge_cents"],
        "currency": "USD",
        "approved_delivery_date": approved_delivery_date.isoformat(),
        "verification_results": verification_checks,
        "authorization_event_id": authorization_event_id,
        "simulated_transaction_id": simulated_transaction_id,
        "record_version": 1,
        "created_at": created_at.isoformat(),
        "status": "confirmed",
        "source": source,
    }
    record["record_hash"] = record_hash(record)
    return record


def integrity(store: BaseStore, record: dict) -> dict:
    return {
        "record_hash": record["record_hash"],
        "hash_matches": record_hash(record) == record["record_hash"],
        "event_chain_valid": verify_event_chain(store, record["purchase_id"]),
        "note": "SHA-256 over the canonical confirmed record. It detects changes relative to a trusted copy of the hash; it does not by itself make storage tamper-proof.",
    }


def is_verified(store: BaseStore, record: dict) -> bool:
    """The one answer a shopper gets: every blocking check passed, and the record and its event chain are intact."""
    i = integrity(store, record)
    return i["hash_matches"] and i["event_chain_valid"] and not any(
        c["status"] == "FAIL" and c.get("blocking", True) for c in record["verification_results"])


PAYMENT_STATUS = {"refunded": "Refunded", "partially_refunded": "Partially refunded", "returned_for_credit": "Refunded as store credit"}
CUSTOMER_FIELDS = ("purchase_id", "kind", "parent_purchase_id", "status", "source", "created_at", "product_id", "product_name",
                   "color", "size", "quantity", "approved_charge_cents", "currency", "approved_delivery_date",
                   "confirmed_requirements", "original_request")
PRICE_FIELDS = ("unit_price_cents", "quantity", "merchandise_cents", "shipping_cents", "shipping_label", "tax_cents",
                "coupon_discount_cents", "charge_cents", "later_statement_credit_cents")


def customer_shipping(store: BaseStore, purchase_id: str) -> dict | None:
    """Tracking steps without carrier-fixture internals (e.g. which variant the simulator delivers)."""
    sh = shipment_for(store, purchase_id)
    if not sh:
        return None
    done = {e["status"]: e["at"] for e in sh["events"]}
    return {
        "status": sh["status"], "carrier": sh["carrier_fixture"], "simulated": True,
        "expected_delivery_date": sh["expected_delivery_date"], "delivered_on": sh["actual_delivery_date"],
        "steps": [{"status": s["status"], "at": done.get(s["status"], s["at"]), "done": s["status"] in done}
                  for s in sh["planned"] if s["status"] != "delayed" or "delayed" in done],
    }


def customer_purchase(store: BaseStore, record: dict) -> dict:
    """What a shopper sees: order, payment and delivery status plus a single verified flag.

    The record hash, event chain, per-rule verification results and internal IDs (authorization event,
    transaction, intent) are never sent here; they are only served by the token-protected /api/admin routes.
    """
    bd = record["full_price_breakdown"]
    pm = record["payment_method_reference"]
    # Seeded history carries a placeholder instead of real checkout checks; it never counts as checks run.
    checks = [c for c in record["verification_results"] if c["status"] in ("PASS", "FAIL") and c.get("rule") != "SEEDED_HISTORY"]
    return {
        **{k: record.get(k) for k in CUSTOMER_FIELDS},
        "price": {k: bd.get(k) for k in PRICE_FIELDS} | {"coupons": [c["code"] for c in bd["coupon_evaluations"] if c["eligible"]]},
        "payment": {"status": "No charge" if record["approved_charge_cents"] == 0 else PAYMENT_STATUS.get(record["status"], "Paid"),
                    "method": pm["label"], "simulated": pm.get("simulated", True)},
        "shipping": customer_shipping(store, record["purchase_id"]),
        "verification": {"verified": is_verified(store, record), "checks_passed": sum(c["status"] == "PASS" for c in checks),
                         "checks_total": len(checks)},
    }


# ---------------------------------------------------------------- shipments


def _at(d: date, hour: int) -> datetime:
    return datetime.combine(d, time(hour, 0), tzinfo=STORE_TZ)


def make_shipment(
    store: BaseStore,
    *,
    shipment_id: str,
    purchase_id: str,
    variant_id: str,
    placed_at: datetime,
    expected: date,
    delivered_variant_id: str | None = None,
    delivered_on: date | None = None,
    label: str = "DemoShip Ground (simulated carrier)",
) -> dict:
    deliver_day = delivered_on or expected
    ship_day = add_business_days(placed_at.date(), 1)
    if ship_day > deliver_day:
        ship_day = deliver_day
    planned = [
        {"status": "label_created", "at": placed_at.isoformat()},
        {"status": "in_transit", "at": _at(ship_day, 9).isoformat()},
        {"status": "out_for_delivery", "at": _at(deliver_day, 8).isoformat()},
        {"status": "delivered", "at": _at(deliver_day, 14).isoformat(),
         "delivered_variant_id": delivered_variant_id or variant_id},
    ]
    if delivered_on and delivered_on > expected:
        planned.insert(2, {"status": "delayed", "at": _at(expected, 12).isoformat(),
                           "note": "Carrier exception: package delayed in transit."})
    shipment = {
        "_id": shipment_id,
        "shipment_id": shipment_id,
        "purchase_id": purchase_id,
        "carrier_fixture": label,
        "status": "pending",
        "events": [],
        "planned": planned,
        "expected_delivery_date": expected.isoformat(),
        "actual_delivery_date": None,
        "delivered_variant_id": None,
        "delivered_at": None,
    }
    store.put("shipments", shipment)
    return shipment


def process_shipments(store: BaseStore, source: str = "live") -> list[dict]:
    """Apply every planned shipment event whose time has arrived on the demo clock."""
    current = now(store)
    changed = []
    for sh in store.find("shipments"):
        applied = {e["status"] for e in sh["events"]}
        new = [p for p in sh["planned"] if p["status"] not in applied and datetime.fromisoformat(p["at"]) <= current]
        if not new:
            continue
        for p in sorted(new, key=lambda p: p["at"]):
            sh["events"].append(p)
            sh["status"] = p["status"] if p["status"] != "delayed" else sh["status"]
            purchase = store.get("purchases", sh["purchase_id"])
            evt_source = purchase.get("source", source) if purchase else source
            if p["status"] == "delayed":
                sh["status"] = "delayed"
            if p["status"] == "delivered":
                sh["actual_delivery_date"] = datetime.fromisoformat(p["at"]).date().isoformat()
                sh["delivered_at"] = p["at"]
                sh["delivered_variant_id"] = p["delivered_variant_id"]
                if purchase and purchase["status"] in ("confirmed", "shipped"):
                    purchase["status"] = "delivered"
                    store.put("purchases", purchase)
            elif p["status"] == "in_transit" and purchase and purchase["status"] == "confirmed":
                purchase["status"] = "shipped"
                store.put("purchases", purchase)
            payload = {k: v for k, v in p.items() if k not in ("at",)}
            if p["status"] == "delivered" and purchase:
                variant_sku = p["delivered_variant_id"]
                payload["delivered_sku_matches_confirmed"] = variant_sku == purchase["variant_id"]
            append_event(store, subject_type="purchase", subject_id=sh["purchase_id"], purchase_id=sh["purchase_id"],
                         event_type=f"shipment.{p['status']}", actor_type="carrier_simulator", payload=payload,
                         source=evt_source, at=datetime.fromisoformat(p["at"]))
        store.put("shipments", sh)
        changed.append(sh)
    return changed


def next_delivery_time(store: BaseStore, purchase_id: str | None = None) -> datetime | None:
    current = now(store)
    times = []
    for sh in store.find("shipments", {"purchase_id": purchase_id} if purchase_id else None):
        for p in sh["planned"]:
            if p["status"] == "delivered" and datetime.fromisoformat(p["at"]) > current:
                times.append(datetime.fromisoformat(p["at"]))
    return min(times) if times else None


def shipment_for(store: BaseStore, purchase_id: str) -> dict | None:
    return store.find_one("shipments", {"purchase_id": purchase_id})


def past(dt: datetime, days: int) -> datetime:
    return dt - timedelta(days=days)
