"""Demo controls: reset, simulated clock, and the merchant price-change scenario."""

from __future__ import annotations

from datetime import timedelta

from .core import DomainError, append_event, now, set_now
from .fixtures import seed
from .records import next_delivery_time, process_shipments
from .store import BaseStore


def reset(store: BaseStore) -> dict:
    seed(store)
    return {"ok": True, "now": now(store).isoformat()}


def advance(store: BaseStore, days: int = 0, hours: int = 0, until: str | None = None, purchase_id: str | None = None) -> dict:
    before = now(store)
    if until in ("next_delivery", "delivered"):
        target = next_delivery_time(store, purchase_id)
        if not target:
            raise DomainError("No pending deliveries to advance to.", 409, "nothing_pending")
        target = target + timedelta(minutes=1)
    else:
        if days == 0 and hours == 0:
            raise DomainError("Advance by at least one hour or day.", 422, "invalid_request")
        target = before + timedelta(days=days, hours=hours)
    set_now(store, target)
    changed = process_shipments(store)
    return {"from": before.isoformat(), "now": target.isoformat(),
            "shipments_updated": [{"shipment_id": s["shipment_id"], "purchase_id": s["purchase_id"], "status": s["status"]} for s in changed]}


def change_price(store: BaseStore, product_id: str, price_cents: int, actor: str = "merchant_admin") -> dict:
    p = store.get("products", product_id)
    if not p:
        raise DomainError("Unknown product.", 404, "not_found")
    old = p["price_cents"]
    p["price_cents"] = price_cents
    store.put("products", p)
    append_event(store, subject_type="product", subject_id=product_id, event_type="catalog.price_changed", actor_type="merchant",
                 payload={"from_cents": old, "to_cents": price_cents, "actor": actor})
    return p
