"""Deterministic price breakdown and offer evaluation. All arithmetic is in integer cents.

Fixture-defined order of operations (documented in README):
  1. merchandise subtotal = unit price x quantity
  2. demonstration tax    = product's flat demo tax x quantity (assessed on the
                            pre-coupon merchandise price; a fixture simplification)
  3. shipping             = selected delivery option price
  4. merchant coupons     = subtracted from merchandise (never below zero)
  5. final charge         = merchandise + shipping + tax - coupons
  6. card-linked offers   = evaluated against their declared price basis. They are
                            *later statement credits* and never reduce the charge.

Shopify products (PERKPILOT_MODE=shopify) price per variant, use the merchant's shipping options, and take tax
from Shopify's own calculation of the exact cart (draftOrderCalculate, with the real discount code applied).
If Shopify can't be asked, tax is unknown and the verifier fails closed.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .core import STORE_TZ, add_business_days, fmt_cents, now
from .store import BaseStore

SHIP_CUTOFF_HOUR = 17


def find_variant(product: dict, variant_id: str) -> dict | None:
    return next((v for v in product["variants"] if v["variant_id"] == variant_id), None)


def unit_price(product: dict, variant: dict | None) -> int:
    """Platform products can price per variant; demo fixtures price per product."""
    return (variant or {}).get("price_cents", product["price_cents"])


def size_label(size: str | None) -> str:
    """'US 10' for numeric (demo shoe) sizes, the size as-is otherwise."""
    if not size:
        return "one size"
    try:
        float(size)
        return f"US {size}"
    except ValueError:
        return size


def sizes_in_order(product: dict) -> list[str]:
    """Smallest to largest: numerically when every size is a number, else the merchant's variant order
    (Shopify lists variants by option position, so S, M, L stays S, M, L)."""
    sizes = list(dict.fromkeys(v["size"] for v in product["variants"] if v["size"]))
    try:
        return sorted(sizes, key=float)
    except ValueError:
        return sizes


APPAREL_SIZES = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "XXXL", "3XL", "4XL", "5XL"]


def _size_rank(size: str, first_seen: int) -> tuple:
    try:
        return (0, float(size), 0)
    except ValueError:
        return (1, APPAREL_SIZES.index(size), 0) if size in APPAREL_SIZES else (2, 0, first_seen)


def catalog_facets(store: BaseStore) -> dict:
    """What the active catalog actually offers; drives the parser, the confirmation form and validation."""
    products = store.find("products", {"status": "active"})
    sizes: list[str] = []
    for p in products:
        sizes += [s for s in sizes_in_order(p) if s not in sizes]
    return {
        "categories": sorted({p["category"] for p in products}),
        "sized_categories": sorted({p["category"] for p in products if any(v["size"] for v in p["variants"])}),
        "colors": sorted({v["color"] for p in products for v in p["variants"] if v["color"]}),
        "sizes": sorted(sizes, key=lambda s: _size_rank(s, sizes.index(s))),  # merged across products, so re-ranked
    }


def find_shipping(product: dict, option_id: str) -> dict | None:
    return next((o for o in product["delivery_options"] if o["id"] == option_id), None)


def estimate_delivery(store: BaseStore, option: dict, at: datetime | None = None) -> date:
    at = (at or now(store)).astimezone(STORE_TZ)
    start = at.date()
    # Orders after the cutoff, or on weekends, start processing the next business day.
    if at.hour >= SHIP_CUTOFF_HOUR or start.weekday() >= 5:
        start = add_business_days(start, 1)
    return add_business_days(start, option["business_days"])


def offer_by_code(store: BaseStore, code: str) -> dict | None:
    code = code.strip().upper()
    return next((o for o in store.find("offers") if (o.get("code") or "").upper() == code), None)


def _expired(offer: dict, at: datetime) -> bool:
    return bool(offer.get("expires_at")) and datetime.fromisoformat(offer["expires_at"]) < at


def evaluate_coupon(store: BaseStore, code: str, product: dict, merchandise_cents: int) -> dict:
    at = now(store)
    offer = offer_by_code(store, code)
    res: dict[str, Any] = {"code": code.upper(), "offer_id": offer["offer_id"] if offer else None, "kind": "coupon"}
    if not offer or offer["offer_type"] != "coupon":
        return {**res, "eligible": False, "reason": f"Coupon {code.upper()} does not exist for this merchant.", "discount_cents": 0}
    res["label"] = offer["label"]
    res["stacking_group"] = offer["stacking_group"]
    if offer["status"] != "active":
        return {**res, "eligible": False, "reason": f"{code.upper()} is not active.", "discount_cents": 0}
    if _expired(offer, at):
        exp = datetime.fromisoformat(offer["expires_at"]).date().isoformat()
        return {**res, "eligible": False, "reason": f"{code.upper()} expired on {exp}.", "discount_cents": 0, "rule": "COUPON_EXPIRED"}
    if offer["eligible_products"] and product["product_id"] not in offer["eligible_products"]:
        return {**res, "eligible": False, "reason": f"{code.upper()} does not apply to {product['name']}.", "discount_cents": 0}
    if merchandise_cents < offer["minimum_spend_cents"]:
        return {
            **res,
            "eligible": False,
            "reason": f"{code.upper()} needs {fmt_cents(offer['minimum_spend_cents'])} merchandise spend; cart has {fmt_cents(merchandise_cents)}.",
            "discount_cents": 0,
        }
    discount = min(offer["amount_cents"] if not offer.get("percentage") else round(merchandise_cents * offer["percentage"] / 100),
                   merchandise_cents)
    return {**res, "eligible": True, "reason": f"{offer['label']} applies.", "discount_cents": discount}


def evaluate_card_offer(store: BaseStore, offer_id: str, payment_method_id: str, basis: dict[str, int]) -> dict:
    """basis maps price-basis names (e.g. 'final_charge') to cent amounts."""
    at = now(store)
    offer = store.get("offers", offer_id)
    res: dict[str, Any] = {"offer_id": offer_id, "kind": "card_linked"}
    if not offer or offer["offer_type"] != "card_linked_statement_credit":
        return {**res, "eligible": False, "reason": "Unknown card-linked offer.", "credit_cents": 0}
    res.update(label=offer["label"], timing="later statement credit", stacking_group=offer["stacking_group"],
               price_basis=offer["eligibility_price_basis"], threshold_cents=offer["minimum_spend_cents"])
    if offer["status"] != "active" or _expired(offer, at):
        return {**res, "eligible": False, "reason": f"{offer['label']} is not currently active.", "credit_cents": 0}
    if payment_method_id not in offer["applicable_payment_methods"]:
        pm = store.get("payment_methods", payment_method_id)
        name = pm["label"] if pm else payment_method_id
        return {**res, "eligible": False, "reason": f"{name} is not enrolled in {offer['label']}.", "credit_cents": 0,
                "observed_payment_method": payment_method_id}
    observed = basis[offer["eligibility_price_basis"]]
    res["observed_cents"] = observed
    if observed < offer["minimum_spend_cents"]:
        return {
            **res,
            "eligible": False,
            "reason": (
                f"Requires a card charge of at least {fmt_cents(offer['minimum_spend_cents'])} after discounts, "
                f"shipping and tax. This charge is {fmt_cents(observed)}."
            ),
            "credit_cents": 0,
        }
    return {**res, "eligible": True, "reason": f"Charge of {fmt_cents(observed)} meets the {fmt_cents(offer['minimum_spend_cents'])} threshold. Credit posts later; it does not reduce today's charge.", "credit_cents": offer["amount_cents"]}


def price_cart(store: BaseStore, product: dict, quantity: int, shipping_option: str, coupon_codes: list[str],
               card_offer_ids: list[str], payment_method_id: str, variant_id: str | None = None) -> dict:
    ship = find_shipping(product, shipping_option)
    variant = find_variant(product, variant_id) if variant_id else None
    unit = unit_price(product, variant)
    merchandise = unit * quantity
    platform = product.get("source") == "shopify"
    tax = 0 if platform else product["demo_tax_cents"] * quantity
    shipping = ship["price_cents"] if ship else 0
    coupon_evals = [evaluate_coupon(store, c, product, merchandise) for c in coupon_codes]

    # Stacking: at most one eligible coupon per stacking group (first one wins, others rejected).
    seen_groups: set[str] = set()
    for ev in coupon_evals:
        if not ev["eligible"]:
            continue
        g = ev.get("stacking_group")
        if g in seen_groups:
            ev.update(eligible=False, discount_cents=0, rule="OFFER_STACKING",
                      reason=f"{ev['code']} cannot be combined with another offer in the '{g}' group.")
        seen_groups.add(g)

    discount = min(sum(ev["discount_cents"] for ev in coupon_evals), merchandise)
    shopify_totals = None
    if platform and ship and variant:
        from .shopify_commerce import calculate

        shopify_totals = calculate(variant, quantity, ship, [ev["code"] for ev in coupon_evals if ev["eligible"]])
        if shopify_totals:
            tax = shopify_totals["tax_cents"]
    charge = merchandise + shipping + tax - discount
    basis = {
        "final_charge": charge,
        "merchandise_subtotal": merchandise,
        "pre_discount_total": merchandise + shipping + tax,
    }
    card_evals = [evaluate_card_offer(store, oid, payment_method_id, basis) for oid in card_offer_ids]
    credits = sum(ev["credit_cents"] for ev in card_evals)
    return {
        "currency": "USD",
        "unit_price_cents": unit,
        "quantity": quantity,
        "merchandise_cents": merchandise,
        "shipping_cents": shipping,
        "shipping_option": shipping_option,
        "shipping_label": ship["label"] if ship else None,
        "tax_cents": tax,
        "pre_discount_total_cents": merchandise + shipping + tax,
        "coupon_discount_cents": discount,
        "charge_cents": charge,
        "later_statement_credit_cents": credits,
        "effective_cost_cents": charge - credits,
        "coupon_evaluations": coupon_evals,
        "card_offer_evaluations": card_evals,
        "tax_source": ("shopify" if shopify_totals else "unavailable") if platform else "demo_fixture",
        "shopify_totals": shopify_totals,
    }
