"""Pydantic contracts shared by the API and the deterministic engines."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# The demo store's categories. Real catalogs (e.g. Shopify) bring their own; see pricing.catalog_facets.
CATEGORIES = [
    "running_shoes",
    "trail_running",
    "walking_shoes",
    "training_shoes",
    "casual_sneakers",
    "recovery_slides",
]

PROBLEM_TYPES = [
    "fit_tight",
    "fit_loose",
    "wrong_item",
    "wrong_color",
    "wrong_size",
    "charge_issue",
    "late_delivery",
    "damaged",
    "preference",
    "unclear",
]


def normalize_size(value: Optional[str | float | int]) -> Optional[str]:
    """Numeric sizes canonicalised ('10.0' -> '10'); anything else (S, XL, 32x30, One Size) upper-cased."""
    s = "" if value is None else str(value).strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return s.upper()[:24]
    if f <= 0:
        return None
    return str(int(f)) if f == int(f) else str(f)


def normalize_category(value: Optional[str]) -> Optional[str]:
    return value.strip().lower().replace(" ", "_") if value and value.strip() else None


class Requirements(BaseModel):
    """Structured purchase requirements. Money is always integer cents."""

    category: Optional[str] = None
    color: Optional[str] = None
    size: Optional[str] = None
    size_system: str = "US"
    max_charge_cents: Optional[int] = Field(default=None, ge=0, le=10_000_00)
    budget_includes_shipping_and_tax: bool = True
    currency: str = "USD"
    deadline_expression: Optional[str] = None
    deadline_date: Optional[str] = None  # ISO date in store timezone
    brand: Optional[str] = None
    payment_method_id: Optional[str] = None
    quantity: int = Field(default=1, ge=1, le=5)

    @field_validator("category")
    @classmethod
    def _cat(cls, v):
        return normalize_category(v)  # membership in the live catalog is checked at confirmation

    @field_validator("size", mode="before")
    @classmethod
    def _size(cls, v):
        return normalize_size(v)

    @field_validator("color")
    @classmethod
    def _color(cls, v):
        return v.strip().lower() if v else None


class ParseRequest(BaseModel):
    shopper_id: Optional[str] = None  # demo personas only; Shopify shoppers are identified by their session token
    text: str = Field(min_length=3, max_length=1000)


class ConfirmIntentRequest(BaseModel):
    intent_id: str
    confirmed_requirements: Requirements
    change_reason: Optional[str] = None


class CartProposal(BaseModel):
    product_id: str
    variant_id: str
    quantity: int = Field(default=1, ge=1, le=5)
    shipping_option: str = "standard"
    coupon_codes: list[str] = []
    card_offer_ids: list[str] = []
    payment_method_id: str = "pm_visa_4242"
    claimed_charge_cents: Optional[int] = None
    claimed_effective_cost_cents: Optional[int] = None
    source: Literal["agent", "shopper", "corrected_option", "exchange", "evaluation"] = "shopper"


class VerifyRequest(BaseModel):
    intent_id: str
    proposal: CartProposal


class CheckoutConfirmRequest(BaseModel):
    quote_id: str
    shopper_id: Optional[str] = None
    approved_charge_cents: int
    shopper_approved: bool


class RecommendRequest(BaseModel):
    intent_id: str
    apply_learned_rules: bool = True


class ClaimRequest(BaseModel):
    purchase_id: str
    shopper_id: Optional[str] = None
    complaint_text: str = Field(min_length=2, max_length=2000)


class ClarifyRequest(BaseModel):
    text: str = Field(min_length=2, max_length=2000)


class RemedyAcceptRequest(BaseModel):
    shopper_id: Optional[str] = None
    confirm: bool
    idempotency_key: Optional[str] = None
    payout_method: Literal["original_payment_method", "push_to_card_concept"] = "original_payment_method"


class OutcomeRequest(BaseModel):
    shopper_id: Optional[str] = None
    outcome: Literal["fits", "still_not_right"]


class RuleDecisionRequest(BaseModel):
    actor: str = "merchant_admin"
    note: Optional[str] = None


class RuleToggleRequest(BaseModel):
    enabled: bool
    actor: str = "merchant_admin"


class AdvanceRequest(BaseModel):
    days: int = Field(default=0, ge=0, le=60)
    hours: int = Field(default=0, ge=0, le=240)
    until: Optional[Literal["next_delivery", "delivered"]] = None
    purchase_id: Optional[str] = None


class StructuredComplaint(BaseModel):
    problem_type: str = "unclear"
    details: str = ""
    requested_outcome: Optional[Literal["exchange", "refund", "store_credit", "none"]] = None
    mentioned_size: Optional[str] = None
    mentioned_color: Optional[str] = None
    missing_information: list[str] = []

    @field_validator("problem_type")
    @classmethod
    def _pt(cls, v):
        if v not in PROBLEM_TYPES:
            raise ValueError(f"unknown problem type '{v}'")
        return v

    @field_validator("mentioned_size", mode="before")
    @classmethod
    def _size(cls, v):
        return normalize_size(v)


class SessionRequest(BaseModel):
    previous_token: Optional[str] = None


class SettingsPatch(BaseModel):
    return_window_days: Optional[int] = Field(default=None, ge=0, le=365)
    preference_return_fee_cents: Optional[int] = Field(default=None, ge=0, le=100_00)
    keep_item_partial_refund_pct: Optional[int] = Field(default=None, ge=0, le=100)
    allow_store_credit: Optional[bool] = None
    auto_execute_test_remedies: Optional[bool] = None
    shipping_options: Optional[list[dict]] = None
    card_offer: Optional[dict] = None
    learning_thresholds: Optional[dict] = None

    @field_validator("shipping_options")
    @classmethod
    def _ship(cls, v):
        if v is None:
            return v
        ids = [o.get("id") for o in v]
        if "standard" not in ids or len(set(ids)) != len(ids):
            raise ValueError("shipping options need unique ids, including 'standard'")
        for o in v:
            if not (isinstance(o.get("price_cents"), int) and 0 <= o["price_cents"] <= 100_00 and isinstance(o.get("business_days"), int)
                    and 0 <= o["business_days"] <= 30 and str(o.get("label") or "").strip()):
                raise ValueError("each shipping option needs a label, price_cents (0-10000) and business_days (0-30)")
        return [{"id": o["id"], "label": o["label"].strip(), "price_cents": o["price_cents"], "business_days": o["business_days"]} for o in v]

    @field_validator("card_offer")
    @classmethod
    def _offer(cls, v):
        if v is None:
            return v
        out = {k: v[k] for k in ("enabled", "label", "credit_cents", "threshold_cents") if k in v}
        for k in ("credit_cents", "threshold_cents"):
            if k in out and not (isinstance(out[k], int) and 0 <= out[k] <= 10_000_00):
                raise ValueError(f"{k} must be whole cents")
        return out


class ScenarioRequest(BaseModel):
    product_id: str
    variant_id: str


class HistoryRequest(BaseModel):
    product_id: str
    size: str
    to_size: str
