"""Shared primitives: demo clock, IDs, money formatting, hashing and the event log."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .store import BaseStore

STORE_TZ = ZoneInfo("America/Los_Angeles")
MERCHANT_ID = "m_stridelab"
CURRENCY = "USD"
# Tuesday of the demonstration week, 10:00 store time.
DEMO_START = datetime(2026, 10, 20, 10, 0, tzinfo=STORE_TZ)


class DomainError(Exception):
    """A rule-level rejection that the API turns into a 4xx with a clear message."""

    def __init__(self, message: str, status: int = 400, code: str = "invalid_request", detail: Any = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------- clock


def now(store: BaseStore) -> datetime:
    meta = store.get("meta", "clock")
    if not meta:
        return DEMO_START
    return datetime.fromisoformat(meta["now"]).astimezone(STORE_TZ)


def set_now(store: BaseStore, value: datetime) -> None:
    store.put("meta", {"_id": "clock", "now": value.astimezone(STORE_TZ).isoformat()})


def today(store: BaseStore) -> date:
    return now(store).date()


def add_business_days(start: date, days: int) -> date:
    cur = start
    added = 0
    while added < days:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            added += 1
    return cur


def end_of_day(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=STORE_TZ)


def wall_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- ids


def next_id(store: BaseStore, prefix: str) -> str:
    """Sequential, human-readable IDs. Deterministic after a demo reset."""
    meta = store.get("meta", "counters") or {"_id": "counters", "values": {}}
    n = meta["values"].get(prefix, 0) + 1
    meta["values"][prefix] = n
    store.put("meta", meta)
    return f"{prefix}_{n:04d}"


# ---------------------------------------------------------------- money / hashing


def fmt_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- event log


def append_event(
    store: BaseStore,
    *,
    subject_type: str,
    subject_id: str,
    event_type: str,
    actor_type: str,
    payload: dict | None = None,
    purchase_id: str | None = None,
    source: str = "live",
    at: datetime | None = None,
) -> dict:
    """Append-only, hash-chained application event.

    Each event references the previous event for the same subject and hashes its own
    body together with that predecessor's hash, so a stored chain can be re-verified
    against a trusted reference later.
    """
    prior = [e for e in store.find("events", {"subject_id": subject_id})]
    prior.sort(key=lambda e: e["seq"])
    prev = prior[-1] if prior else None
    seq_meta = store.get("meta", "event_seq") or {"_id": "event_seq", "value": 0}
    seq_meta["value"] += 1
    store.put("meta", seq_meta)
    body = {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "purchase_id": purchase_id,
        "event_type": event_type,
        "timestamp": (at or now(store)).isoformat(),
        "wall_time": wall_now(),
        "actor_type": actor_type,
        "payload": payload or {},
        "prior_event_reference": prev["_id"] if prev else None,
        "merchant_id": MERCHANT_ID,
        "source": source,
    }
    event_hash = sha256_hex({"prev": prev["event_hash"] if prev else None, "body": body})
    event = {"_id": f"evt_{seq_meta['value']:05d}", "event_id": f"evt_{seq_meta['value']:05d}", "seq": seq_meta["value"], **body, "event_hash": event_hash}
    store.put("events", event)
    return event


def verify_event_chain(store: BaseStore, subject_id: str) -> bool:
    events = sorted(store.find("events", {"subject_id": subject_id}), key=lambda e: e["seq"])
    prev_hash = None
    prev_id = None
    for e in events:
        body = {k: e[k] for k in (
            "subject_type", "subject_id", "purchase_id", "event_type", "timestamp", "wall_time",
            "actor_type", "payload", "prior_event_reference", "merchant_id", "source",
        )}
        if e["prior_event_reference"] != prev_id:
            return False
        if sha256_hex({"prev": prev_hash, "body": body}) != e["event_hash"]:
            return False
        prev_hash, prev_id = e["event_hash"], e["_id"]
    return True
