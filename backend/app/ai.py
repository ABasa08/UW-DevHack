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
import time
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any

import httpx
from pydantic import ValidationError

from .models import CATEGORIES, PROBLEM_TYPES, Requirements, StructuredComplaint, normalize_size
from .pricing import size_label

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
COLORS = ["black", "white", "grey", "gray", "navy", "blue", "red", "green", "sand", "orange"]
BRANDS = ["stridelab", "ridgeback", "metro", "forge"]
OTHER_BRANDS = [
    (r"\bjordans?\b", "Nike Jordan"), (r"\bnike\b", "Nike"), (r"\badidas\b", "Adidas"), (r"\bnew balance\b", "New Balance"),
    (r"\basics\b", "Asics"), (r"\bhoka\b", "Hoka"), (r"\bbrooks\b", "Brooks"), (r"\bsaucony\b", "Saucony"),
    (r"\bpuma\b", "Puma"), (r"\breebok\b", "Reebok"), (r"\bon cloud\w*\b", "On"), (r"\bvans\b", "Vans"),
    (r"\bconverse\b", "Converse"), (r"\bsalomon\b", "Salomon"), (r"\ballbirds\b", "Allbirds"),
]


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
    return os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")


def gemini_models() -> list[str]:
    """Primary model, then an optional backup tried when the primary is overloaded."""
    backup = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite").strip()
    return [m for m in dict.fromkeys([gemini_model(), backup]) if m]


TRANSIENT = {429, 500, 502, 503, 504}
TIME_BUDGET_S = 10.0  # total time allowed for all Gemini attempts before using the fallback interpreter


def _post_gemini(model: str, body: dict, key: str, timeout: float) -> dict:
    resp = httpx.post(GEMINI_URL.format(model=model), json=body, timeout=timeout,
                      headers={"x-goog-api-key": key, "content-type": "application/json"})
    resp.raise_for_status()
    parts = resp.json()["candidates"][0]["content"]["parts"]
    # Thinking models may return thought parts; the answer is the first non-thought text part.
    text = next(p["text"] for p in parts if "text" in p and not p.get("thought"))
    return json.loads(text)


def _call_gemini(system: str, user: str, schema: dict) -> tuple[dict, str]:
    key = os.environ["GEMINI_API_KEY"].strip()
    config: dict[str, Any] = {"temperature": 0, "responseMimeType": "application/json", "responseSchema": schema}
    level = os.environ.get("GEMINI_THINKING_LEVEL", "low").strip()
    if level:
        config["thinkingConfig"] = {"thinkingLevel": level}
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": config,
    }
    deadline = time.monotonic() + TIME_BUDGET_S
    last: Exception | None = None
    for model in gemini_models():
        for attempt in range(2):  # one quick retry on transient overload before moving to the backup model
            remaining = deadline - time.monotonic()
            if remaining < 1.0:
                raise last or httpx.TimeoutException("Gemini time budget exhausted")
            try:
                return _post_gemini(model, body, key, remaining), model
            except httpx.HTTPStatusError as exc:
                last = exc
                code = exc.response.status_code
                if code == 400 and "thinking" in exc.response.text.lower() and "thinkingConfig" in config:
                    config.pop("thinkingConfig")  # model doesn't support this thinking level; retry plainly
                    continue
                if code not in TRANSIENT:
                    raise
                if attempt == 0:
                    time.sleep(0.5)
            except httpx.TimeoutException as exc:
                last = exc
                break
    raise last or httpx.TimeoutException("Gemini unavailable")


def _interpret(system: str, user: str, schema: dict, fallback, validate) -> tuple[Any, dict]:
    status = ai_status()
    if status["mode"] == "gemini":
        try:
            raw, model = _call_gemini(system, user, schema)
            return validate(raw), {"mode": "gemini", "model": model, "note": "Live Gemini interpretation, validated against the backend schema."}
        except (httpx.HTTPError, KeyError, IndexError, StopIteration, json.JSONDecodeError, ValidationError, ValueError) as exc:
            detail = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
            result = fallback()
            return result, {"mode": "fallback", "model": None,
                            "note": f"Gemini output unavailable or rejected by validation ({detail}); used deterministic fallback interpreter."}
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

def _req_schema(categories: list[str]) -> dict:
    return {
        "type": "OBJECT",
        "properties": {
            "category": {"type": "STRING", "enum": categories, "nullable": True},
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


def _req_system(c: dict) -> str:
    sizes = f"Sizes offered: {c['sizes'][:40]}; map the shopper's words to one of these ('medium'->'M')." if c["sizes"] else ""
    return (
        "You extract structured shopping requirements for an online store. "
        "Only record what the shopper actually said. Never invent preferences: leave a field null when it was not stated. "
        f"category must be one of {c['categories']}; pick the closest match for what the shopper describes. "
        f"Products and their categories: {dict(list(c.get('titles', {}).items())[:60])}. {sizes} "
        "max_charge_dollars is the maximum amount the shopper is willing to be charged. Set budget_includes_shipping_and_tax true only when "
        "the shopper says the budget includes shipping/tax/everything/total, false if they say it excludes them, null if unstated. "
        "deadline_expression is the shopper's words for the delivery deadline (e.g. 'Friday'); do not convert it to a date. "
        "Add an ambiguity (field + short question) for anything essential that is missing or unclear: category, max budget, "
        f"and size when the category is one of {c['sized_categories']}. "
        "assistant_message: one or two friendly sentences restating what you understood, with no prices or promises."
    )


DEMO_FACETS = {"categories": CATEGORIES, "sized_categories": CATEGORIES, "colors": [], "sizes": []}
SIZE_WORDS = {"extra small": "XS", "x-small": "XS", "small": "S", "medium": "M", "large": "L", "extra large": "XL", "x-large": "XL"}


def _fallback_requirements(text: str, catalog: dict = DEMO_FACETS) -> dict:
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
    if out.get("category") not in catalog["categories"]:
        # Any other catalog: the whole category name ("tank top"), else a word that pins down exactly one category,
        # from its name ("sweater" -> men's_sweater) or a product title ("hoodie" -> fleece). Plurals match too.
        out.pop("category", None)
        by_word: dict[str, set] = {}
        for text_, c in [(c.replace("_", " "), c) for c in catalog["categories"]] + list(catalog.get("titles", {}).items()):
            for w in re.findall(r"[a-z]{4,}", text_.lower()):
                by_word.setdefault(w.rstrip("s"), set()).add(c)
        names = [(c.replace("_", " "), c) for c in catalog["categories"]] + [(w, next(iter(cs))) for w, cs in by_word.items() if len(cs) == 1]
        out["category"] = next((c for w, c in names if re.search(rf"\b{re.escape(w.rstrip('s'))}s?\b", t)), None)
    for c in [*catalog["colors"], *COLORS]:
        if re.search(rf"\b{re.escape(c)}\b", t):
            out["color"] = "grey" if c == "gray" else c
            break
    m = re.search(r"size\s*(?:us\s*)?(\d{1,2}(?:\.5)?)|(\d{1,2}(?:\.5)?)\s*(?:us\b|men'?s|women'?s)", t)
    if m:
        out["size"] = m.group(1) or m.group(2)
    else:
        m = re.search(r"\bsize\s+([a-z0-9][\w./-]*)", t)
        # Size words ("large"), or a multi-letter catalog size said bare ("xs", "2xl"); single letters are too ambiguous.
        bare = [(s.lower(), s) for s in catalog["sizes"] if len(s) > 1 and not s.replace(".", "").isdigit()]
        word = next((code for w, code in sorted([*SIZE_WORDS.items(), *bare], key=lambda kv: -len(kv[0]))
                     if re.search(rf"\b{re.escape(w)}\b", t)), None)
        for cand in (normalize_size(SIZE_WORDS.get(m.group(1), m.group(1))) if m else None, word):
            if cand and cand in catalog["sizes"]:
                out["size"] = cand
                break
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
    if "brand" not in out:
        # Common brands this store doesn't carry: recognise them so the shopper is told, rather than silently ignored.
        for pattern, name in OTHER_BRANDS:
            if re.search(pattern, t):
                out["brand"] = name
                break
    if "debit" in t:
        out["payment_preference"] = "debit"
    m = re.search(r"\b(two|three|2|3)\s+pairs?\b", t)
    if m:
        out["quantity"] = {"two": 2, "three": 3}.get(m.group(1), None) or int(m.group(1))
    parts = []
    if out.get("color"):
        parts.append(out["color"])
    parts.append((out.get("category") or "item").replace("_", " "))
    if out.get("size"):
        parts.append(f"in {size_label(out['size'])}")
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


def parse_shopping_request(text: str, today: date, catalog: dict | None = None) -> dict:
    catalog = catalog or DEMO_FACETS
    holder: dict = {}

    def validate(raw):
        holder["parsed"] = _validated_requirements(raw, today)
        return holder["parsed"]

    def fallback():
        return _validated_requirements(_fallback_requirements(text, catalog), today)

    user = f"Today's date in the store timezone is {today.isoformat()} ({WEEKDAYS[today.weekday()].title()}).\nShopper request: {text}"
    (req, amb, notes, message), meta = _interpret(_req_system(catalog), user, _req_schema(catalog["categories"] or ["other"]), fallback, validate)

    # Deterministic completeness check on top of whatever the interpreter said.
    essentials = {"category": "What type of product are you shopping for?", "size": "What size do you need?",
                  "max_charge_cents": "What's the most you want to be charged, including shipping and tax?"}
    if req.category and req.category not in catalog["sized_categories"]:
        essentials.pop("size")
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

RULE_SCHEMA = {"type": "OBJECT", "properties": {"merchant_summary": {"type": "STRING"}}, "required": ["merchant_summary"]}


def draft_rule_text(facts: dict) -> tuple[dict, dict]:
    """The shopper-facing sentence is fixed wording; Gemini only drafts the merchant's summary."""
    customer_message = (f"Some customers reported a tight fit in size {facts['from_size']}. "
                        f"Would you like to review size {facts['to_size']}?")

    def fallback():
        return {
            "customer_message": customer_message,
            "merchant_summary": (f"{facts['validated']} confirmed fit-related exchanges from size {facts['from_size']} to "
                                 f"{facts['to_size']} among {facts['fulfilled']} fulfilled size-{facts['from_size']} orders of "
                                 f"{facts['product_name']} suggest shoppers ordering size {facts['from_size']} may benefit from "
                                 f"reviewing size {facts['to_size']}."),
        }

    def validate(raw):
        summary = str(raw["merchant_summary"]).strip()
        # Must restate the real counts and sizes, and must not introduce other numbers.
        numbers = set(re.findall(r"\d+(?:\.\d+)?", summary))
        allowed = {str(facts["validated"]), str(facts["fulfilled"]), facts["from_size"], facts["to_size"]}
        if not summary or len(summary) > 400 or not allowed <= numbers or not numbers <= allowed:
            raise ValueError("draft failed wording constraints")
        return {"customer_message": customer_message, "merchant_summary": summary}

    system = ("Write a one- or two-sentence summary for a merchant reviewing a proposed size-guidance rule. State the number of "
              "confirmed fit exchanges, the number of fulfilled orders, and both sizes, using exactly the numbers given and no others. "
              "Neutral tone: do not claim a product defect and do not overstate the evidence.")
    return _interpret(system, json.dumps(facts), RULE_SCHEMA, fallback, validate)
