"""Language interpretation layer.

Gemini (when GEMINI_API_KEY is set) turns shopper language into *proposed* structured
data. Every model output is validated with Pydantic before use; invalid output is
discarded. When Gemini is unavailable, a deterministic rule-based interpreter is used
and the response says so explicitly — it is never presented as live inference.

Nothing in this module approves purchases, changes amounts, picks remedies or activates
rules.
"""

from __future__ import annotations

import contextvars
import json
import os
import re
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any

import httpx
from pydantic import ValidationError

from .models import CATEGORIES, PROBLEM_TYPES, Requirements, StructuredComplaint, normalize_size

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
COLORS = ["black", "white", "grey", "gray", "navy", "blue", "red", "green", "sand", "orange"]
BRANDS = ["stridelab", "ridgeback", "metro", "forge"]


_force_fallback: contextvars.ContextVar[bool] = contextvars.ContextVar("force_fallback", default=False)


@contextmanager
def deterministic_interpreter():
    """Force the fallback interpreter (used by sandboxed evaluation runs for repeatability)."""
    token = _force_fallback.set(True)
    try:
        yield
    finally:
        _force_fallback.reset(token)


def ai_status() -> dict:
    mode = "fallback" if _force_fallback.get() else os.environ.get("PERKPILOT_AI_MODE", "auto").lower()
    has_key = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    live = has_key and mode != "fallback"
    return {
        "mode": "gemini" if live else "fallback",
        "model": gemini_model() if live else None,
        "note": None if live else (
            "Gemini disabled (PERKPILOT_AI_MODE=fallback)." if has_key else
            "No GEMINI_API_KEY configured; using the deterministic fallback interpreter."
        ),
    }


def gemini_model() -> str:
    return os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


def _call_gemini(system: str, user: str, schema: dict, timeout: float = 12.0) -> dict:
    key = os.environ["GEMINI_API_KEY"].strip()
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": schema},
    }
    resp = httpx.post(GEMINI_URL.format(model=gemini_model()), json=body, timeout=timeout,
                      headers={"x-goog-api-key": key, "content-type": "application/json"})
    resp.raise_for_status()
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def _interpret(system: str, user: str, schema: dict, fallback, validate) -> tuple[Any, dict]:
    status = ai_status()
    if status["mode"] == "gemini":
        try:
            raw = _call_gemini(system, user, schema)
            return validate(raw), {"mode": "gemini", "model": status["model"], "note": "Live Gemini interpretation, validated against the backend schema."}
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            result = fallback()
            return result, {"mode": "fallback", "model": None,
                            "note": f"Gemini output unavailable or rejected by validation ({type(exc).__name__}); used deterministic fallback interpreter."}
    return fallback(), {"mode": "fallback", "model": None, "note": status["note"]}


# ---------------------------------------------------------------- dates


def resolve_deadline(expression: str | None, today: date) -> str | None:
    """Deterministically resolve a relative delivery deadline using the demo clock."""
    if not expression:
        return None
    e = expression.strip().lower()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", e):
        return e
    if "tomorrow" in e:
        return (today + timedelta(days=1)).isoformat()
    if "today" in e:
        return today.isoformat()
    m = re.search(r"in (\d+) days?", e)
    if m:
        return (today + timedelta(days=int(m.group(1)))).isoformat()
    for i, wd in enumerate(WEEKDAYS):
        if wd in e or (len(e) >= 3 and re.search(rf"\b{wd[:3]}\b", e)):
            delta = (i - today.weekday()) % 7
            if "next" in e and delta == 0:
                delta = 7
            return (today + timedelta(days=delta)).isoformat()
    m = re.search(r"(" + "|".join(MONTHS) + r")[a-z]*\.?\s+(\d{1,2})", e)
    if m:
        month = MONTHS.index(m.group(1)) + 1
        year = today.year if month >= today.month else today.year + 1
        try:
            return date(year, month, int(m.group(2))).isoformat()
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------- shopping requests

REQ_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "category": {"type": "STRING", "enum": CATEGORIES, "nullable": True},
        "color": {"type": "STRING", "nullable": True},
        "size": {"type": "STRING", "nullable": True},
        "max_charge_dollars": {"type": "NUMBER", "nullable": True},
        "budget_includes_shipping_and_tax": {"type": "BOOLEAN", "nullable": True},
        "deadline_expression": {"type": "STRING", "nullable": True},
        "brand": {"type": "STRING", "nullable": True},
        "payment_preference": {"type": "STRING", "enum": ["credit", "debit"], "nullable": True},
        "quantity": {"type": "INTEGER", "nullable": True},
        "ambiguities": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "field": {"type": "STRING"}, "question": {"type": "STRING"}}, "required": ["field", "question"]}},
        "assistant_message": {"type": "STRING"},
    },
    "required": ["ambiguities", "assistant_message"],
}

REQ_SYSTEM = (
    "You extract structured shoe-shopping requirements for the fictional store StrideLab Running. "
    "Only record what the shopper actually said. Never invent preferences: leave a field null when it was not stated. "
    f"category must be one of {CATEGORIES} ('running shoes'->running_shoes). Sizes are US sizes as strings like '10' or '10.5'. "
    "max_charge_dollars is the maximum amount the shopper is willing to be charged. Set budget_includes_shipping_and_tax true only when "
    "the shopper says the budget includes shipping/tax/everything/total, false if they say it excludes them, null if unstated. "
    "deadline_expression is the shopper's words for the delivery deadline (e.g. 'Friday'); do not convert it to a date. "
    "Add an ambiguity (field + short question) for anything essential that is missing or unclear: category, size, max budget. "
    "assistant_message: one or two friendly sentences restating what you understood, with no prices or promises."
)


def _fallback_requirements(text: str) -> dict:
    t = text.lower()
    out: dict[str, Any] = {"ambiguities": []}
    if "trail" in t:
        out["category"] = "trail_running"
    elif re.search(r"\brun(ning|ners?|s)?\b|\bjog", t):
        out["category"] = "running_shoes"
    elif "walk" in t:
        out["category"] = "walking_shoes"
    elif re.search(r"gym|training|trainer|lifting|hiit", t):
        out["category"] = "training_shoes"
    elif re.search(r"slide|sandal|recovery", t):
        out["category"] = "recovery_slides"
    elif re.search(r"sneaker|casual|court", t):
        out["category"] = "casual_sneakers"
    for c in COLORS:
        if re.search(rf"\b{c}\b", t):
            out["color"] = "grey" if c == "gray" else c
            break
    m = re.search(r"size\s*(?:us\s*)?(\d{1,2}(?:\.5)?)|(\d{1,2}(?:\.5)?)\s*(?:us\b|men'?s|women'?s)", t)
    if m:
        out["size"] = m.group(1) or m.group(2)
    m = re.search(r"\$\s?(\d{1,4}(?:\.\d{1,2})?)|(\d{2,4})\s*(?:dollars|usd|bucks)", t)
    if m:
        out["max_charge_dollars"] = float(m.group(1) or m.group(2))
        if re.search(r"includ|total|everything|all[- ]in|shipping and tax|tax and shipping|checkout charge|out the door", t):
            out["budget_includes_shipping_and_tax"] = True
        elif re.search(r"before (shipping|tax)|plus (shipping|tax)|excluding", t):
            out["budget_includes_shipping_and_tax"] = False
    m = re.search(r"(?:by|before|no later than|arrive|arrives|delivered)\s+(?:by\s+)?((?:next\s+)?(?:" + "|".join(WEEKDAYS) +
                  r")|tomorrow|today|in \d+ days?|(?:" + "|".join(MONTHS) + r")[a-z]*\.?\s+\d{1,2}|\d{4}-\d{2}-\d{2})", t)
    if m:
        out["deadline_expression"] = m.group(1)
    for b in BRANDS:
        if b in t:
            out["brand"] = {"stridelab": "StrideLab", "ridgeback": "Ridgeback", "metro": "Metro", "forge": "Forge"}[b]
    if "debit" in t:
        out["payment_preference"] = "debit"
    m = re.search(r"\b(two|three|2|3)\s+pairs?\b", t)
    if m:
        out["quantity"] = {"two": 2, "three": 3}.get(m.group(1), None) or int(m.group(1))
    parts = []
    if out.get("color"):
        parts.append(out["color"])
    parts.append((out.get("category") or "shoes").replace("_", " "))
    if out.get("size"):
        parts.append(f"in US size {out['size']}")
    out["assistant_message"] = f"Here's what I understood: {' '.join(parts)}. Please check the details below before I search."
    return out


def _validated_requirements(raw: dict, today: date) -> tuple[Requirements, list[dict], list[str], str]:
    dollars = raw.get("max_charge_dollars")
    includes = raw.get("budget_includes_shipping_and_tax")
    notes: list[str] = []
    if dollars is not None and includes is None:
        includes = True
        notes.append("You didn't say whether the budget includes shipping and tax, so PerkPilot treats it as your maximum total charge (the stricter reading). Edit it below if that's wrong.")
    deadline_expr = raw.get("deadline_expression")
    pm = None
    if raw.get("payment_preference") == "debit":
        pm = "pm_visa_1881"
    req = Requirements(
        category=raw.get("category"),
        color=raw.get("color"),
        size=normalize_size(raw.get("size")),
        max_charge_cents=round(float(dollars) * 100) if dollars is not None else None,
        budget_includes_shipping_and_tax=bool(includes) if includes is not None else True,
        deadline_expression=deadline_expr,
        deadline_date=resolve_deadline(deadline_expr, today),
        brand=raw.get("brand"),
        payment_method_id=pm,
        quantity=raw.get("quantity") or 1,
    )
    if deadline_expr and not req.deadline_date:
        notes.append(f"Couldn't resolve the deadline '{deadline_expr}' to a date; please pick one.")
    amb = [a for a in raw.get("ambiguities", []) if isinstance(a, dict) and a.get("field") and a.get("question")]
    return req, amb, notes, str(raw.get("assistant_message") or "")


def parse_shopping_request(text: str, today: date) -> dict:
    holder: dict = {}

    def validate(raw):
        holder["parsed"] = _validated_requirements(raw, today)
        return holder["parsed"]

    def fallback():
        return _validated_requirements(_fallback_requirements(text), today)

    user = f"Today's date in the store timezone is {today.isoformat()} ({WEEKDAYS[today.weekday()].title()}).\nShopper request: {text}"
    (req, amb, notes, message), meta = _interpret(REQ_SYSTEM, user, REQ_SCHEMA, fallback, validate)

    # Deterministic completeness check on top of whatever the interpreter said.
    essentials = {"category": "What type of shoe are you shopping for?", "size": "What US size do you need?",
                  "max_charge_cents": "What's the most you want to be charged, including shipping and tax?"}
    have = {a["field"] for a in amb}
    for field, q in essentials.items():
        if getattr(req, field) is None and field not in have and not (field == "max_charge_cents" and "max_charge_dollars" in have):
            amb.append({"field": field, "question": q})
    amb = [a for a in amb if not (a["field"] in ("max_charge_dollars", "max_charge_cents") and req.max_charge_cents is not None)
           and not (a["field"] in ("category", "size", "color") and getattr(req, a["field"], None) is not None)]
    missing = [f for f in essentials if getattr(req, f) is None]
    return {"requirements": req.model_dump(), "ambiguities": amb, "notes": notes, "missing_essential": missing,
            "assistant_message": message, "interpreter": meta}


# ---------------------------------------------------------------- complaints

CLAIM_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "problem_type": {"type": "STRING", "enum": PROBLEM_TYPES},
        "details": {"type": "STRING"},
        "requested_outcome": {"type": "STRING", "enum": ["exchange", "refund", "store_credit", "none"], "nullable": True},
        "mentioned_size": {"type": "STRING", "nullable": True},
        "mentioned_color": {"type": "STRING", "nullable": True},
        "missing_information": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["problem_type", "details", "missing_information"],
}

CLAIM_SYSTEM = (
    "You turn a shopper's post-purchase complaint into structured data. Report only what the shopper said; do not decide who is at fault "
    "and do not invent evidence. problem_type: fit_tight (too small/tight/narrow), fit_loose (too big/loose), wrong_item, wrong_color, "
    "wrong_size (received a different size than ordered), charge_issue, late_delivery, damaged, preference (changed their mind / prefers "
    "something else although nothing is wrong), unclear (not enough information). requested_outcome only if stated."
)


def _fallback_complaint(text: str) -> dict:
    t = text.lower()
    pt = "unclear"
    if re.search(r"changed my mind|change my mind|rather have|decided|don'?t like the (colou?r|look|style)", t):
        pt = "preference"
    elif re.search(r"tight|too small|small|narrow|pinch|cramp|squeez|snug", t):
        pt = "fit_tight"
    elif re.search(r"loose|too big|big|large|slipp|roomy", t):
        pt = "fit_loose"
    elif re.search(r"wrong colou?r|different colou?r|colou?r is wrong|(white|black|navy|grey|blue|red|green) instead|not (black|white)", t):
        pt = "wrong_color"
    elif re.search(r"wrong size|different size|labell?ed size", t):
        pt = "wrong_size"
    elif re.search(r"wrong (item|shoe|product|model)|not what i ordered|different shoe", t):
        pt = "wrong_item"
    elif re.search(r"charg|billed|overcharg|price|paid more|refund the difference", t):
        pt = "charge_issue"
    elif re.search(r"late|hasn'?t arrived|didn'?t arrive|not arrived|delay|still waiting|after the promised", t):
        pt = "late_delivery"
    elif re.search(r"damag|broken|torn|ripped|defect", t):
        pt = "damaged"
    elif re.search(r"prefer", t):
        pt = "preference"
    ro = None
    if re.search(r"exchange|swap|bigger|half size|different size|replace", t):
        ro = "exchange"
    elif re.search(r"refund|money back|return it", t):
        ro = "refund"
    elif re.search(r"store credit|credit", t):
        ro = "store_credit"
    m = re.search(r"size\s*(\d{1,2}(?:\.5)?)", t)
    col = next((c for c in COLORS if re.search(rf"\b{c}\b", t)), None)
    return {"problem_type": pt, "details": text.strip()[:300], "requested_outcome": ro,
            "mentioned_size": m.group(1) if m else None, "mentioned_color": col,
            "missing_information": ["what specifically is different from what you expected"] if pt == "unclear" else []}


def parse_complaint(text: str, order_summary: str) -> tuple[dict, dict]:
    def validate(raw):
        return StructuredComplaint(**raw).model_dump()

    def fallback():
        return StructuredComplaint(**_fallback_complaint(text)).model_dump()

    user = f"Order on file: {order_summary}\nShopper message: {text}"
    return _interpret(CLAIM_SYSTEM, user, CLAIM_SCHEMA, fallback, validate)


# ---------------------------------------------------------------- rule wording

RULE_SCHEMA = {"type": "OBJECT", "properties": {"customer_message": {"type": "STRING"}, "merchant_summary": {"type": "STRING"}},
               "required": ["customer_message", "merchant_summary"]}


def draft_rule_text(facts: dict) -> tuple[dict, dict]:
    def fallback():
        return {
            "customer_message": (f"Some customers reported a tight fit in size {facts['from_size']}. "
                                 f"Would you like to review size {facts['to_size']}?"),
            "merchant_summary": (f"{facts['validated']} confirmed fit-related exchanges from size {facts['from_size']} to "
                                 f"{facts['to_size']} among {facts['fulfilled']} fulfilled size-{facts['from_size']} orders of "
                                 f"{facts['product_name']} suggest shoppers ordering size {facts['from_size']} may benefit from "
                                 f"reviewing size {facts['to_size']}."),
        }

    def validate(raw):
        msg = str(raw["customer_message"]).strip()
        summary = str(raw["merchant_summary"]).strip()
        # The draft must not invent numbers or change sizes: require both sizes present and length limits.
        if not msg or len(msg) > 220 or facts["to_size"] not in msg or facts["from_size"] not in msg:
            raise ValueError("draft failed wording constraints")
        if not summary or len(summary) > 500:
            raise ValueError("draft failed wording constraints")
        return {"customer_message": msg, "merchant_summary": summary}

    system = ("Draft short, neutral wording for a merchant-reviewed size guidance note. Do not claim a defect, do not use urgency, "
              "do not add numbers that are not in the facts. The customer message must be a question offering to review the other size "
              "and must mention both sizes.")
    return _interpret(system, json.dumps(facts), RULE_SCHEMA, fallback, validate)
