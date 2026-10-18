"""Shopify as PerkPilot's commerce platform (PERKPILOT_MODE=shopify).

PerkPilot keeps what the shopper approved, the deterministic checks, the claim and the learning loop.
Shopify does the commerce, and every fact PerkPilot records about a purchase comes back from Shopify:

* totals: tax, discounts and the order total come from ``draftOrderCalculate``. The real discount code is applied
  by Shopify, so Shopify checks the coupon a second time.
* checkout: an approved quote becomes a draft order with exactly the verified lines, code and shipping line, and
  inventory reserved. The shopper pays on Shopify's own checkout page (test mode in a development store).
* purchase record: created when the Shopify order exists. The order total, payment, card brand, fulfillments and
  refunds are read back from Shopify (``reconcile_order``), never typed in by PerkPilot.
* remedies: refunds go through ``refundCreate`` and exchanges are free replacement orders plus a return on the
  original. Only test orders execute automatically; for real orders the merchant approves each remedy.
* learning: approved fit guidance is written to the product metafield ``perkpilot.fit_guidance``, which the
  theme block renders on the product page.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from . import shopify_api as api
from .core import MERCHANT_ID, POLICY_ID, STORE_TZ, DomainError, add_business_days, append_event, fmt_cents, next_id, now
from .records import make_purchase_record
from .shopify import PREFIX, _numeric_id
from .store import BaseStore

CARD_OFFER_ID = "clo_visa"
PM_VISA = "pm_shopify_visa"
PM_OTHER = "pm_shopify_other"
TEST_CARRIER = "PerkPilot test carrier"


def cents(amount) -> int:
    return int(Decimal(str(amount or "0")) * 100)


def _money_set(node: dict | None, key: str) -> int:
    return cents(((node or {}).get(key) or {}).get("shopMoney", {}).get("amount"))


# ---------------------------------------------------------------- merchant defaults and settings


def default_policy() -> dict:
    from .fixtures import policy

    return {**policy(),
            "shipping_options": [
                {"id": "standard", "label": "Standard (3 business days)", "price_cents": 8_00, "business_days": 3},
                {"id": "express", "label": "Express (1 business day)", "price_cents": 20_00, "business_days": 1},
            ],
            "card_offer": {"enabled": True, "label": "Visa card-linked offer", "credit_cents": 10_00, "threshold_cents": 120_00},
            "auto_execute_test_remedies": True,
            "learning_thresholds": {"min_fulfilled_orders": 20, "min_validated_outcomes": 3,
                                    "note": "Illustrative pilot thresholds, not statistically validated."}}


def ensure_defaults(store: BaseStore, shop_name: str | None = None) -> None:
    """Merchant, policy, Shopify payment choices and the card-linked offer exist before the first shopper."""
    if not store.get("merchants", MERCHANT_ID):
        store.put("merchants", {"_id": MERCHANT_ID, "merchant_id": MERCHANT_ID, "name": shop_name or "Your store",
                                "tagline": "Shopify store", "timezone": "America/Los_Angeles", "platform": "shopify"})
    if not store.get("policies", POLICY_ID):
        store.put("policies", {"_id": POLICY_ID, **{k: v for k, v in default_policy().items() if k != "_id"}})
    store.put("payment_methods", {
        "_id": PM_VISA, "payment_method_id": PM_VISA, "label": "Visa card at Shopify checkout", "network": "visa", "type": "card",
        "enrolled_offers": [CARD_OFFER_ID], "simulated": False,
        "note": "You enter the card on Shopify's checkout. PerkPilot never sees card numbers; after payment it reads the "
                "card brand and last four digits from the Shopify order."})
    store.put("payment_methods", {
        "_id": PM_OTHER, "payment_method_id": PM_OTHER, "label": "Another payment method at Shopify checkout", "network": "other",
        "type": "other", "enrolled_offers": [], "simulated": False, "note": "Any payment method Shopify checkout offers."})
    apply_settings(store)


def apply_settings(store: BaseStore) -> None:
    """Push policy shipping options onto Shopify products and keep the card-linked offer document in sync."""
    pol = store.get("policies", POLICY_ID)
    if not pol or "shipping_options" not in pol:
        return
    for p in store.find("products"):
        if p.get("source") == "shopify" and p.get("delivery_options") != pol["shipping_options"]:
            store.put("products", {**p, "delivery_options": pol["shipping_options"]})
    co = pol["card_offer"]
    store.put("offers", {
        "_id": CARD_OFFER_ID, "offer_id": CARD_OFFER_ID, "merchant_id": MERCHANT_ID, "offer_type": "card_linked_statement_credit",
        "code": None, "label": f"{co['label']}: {fmt_cents(co['credit_cents'])} statement credit on {fmt_cents(co['threshold_cents'])}+",
        "amount_cents": co["credit_cents"], "percentage": None, "minimum_spend_cents": co["threshold_cents"], "eligible_products": [],
        "applicable_payment_methods": [PM_VISA], "stacking_group": "card_linked", "expires_at": None,
        "eligibility_price_basis": "final_charge", "status": "active" if co["enabled"] else "inactive", "synthetic_fixture": True,
        "terms": "Simulated issuer program (no real Visa enrollment). The credit would post after settlement when the actual "
                 "Shopify charge, after discounts and including shipping and tax, meets the threshold. It never reduces the charge."})


SETTINGS_KEYS = ("return_window_days", "preference_return_fee_cents", "keep_item_partial_refund_pct", "allow_store_credit",
                 "shipping_options", "card_offer", "auto_execute_test_remedies", "learning_thresholds")


def update_settings(store: BaseStore, patch: dict) -> dict:
    pol = store.get("policies", POLICY_ID)
    for k, v in patch.items():
        if k in SETTINGS_KEYS and v is not None:
            pol[k] = {**pol[k], **v} if isinstance(pol.get(k), dict) and isinstance(v, dict) else v
    store.put("policies", pol)
    apply_settings(store)
    return {k: pol.get(k) for k in SETTINGS_KEYS}


# ---------------------------------------------------------------- discount codes -> offers


def map_discount(node: dict) -> dict | None:
    """A basic code discount -> PerkPilot coupon offer. Other discount kinds are out of scope and skipped."""
    d = node.get("codeDiscount") or {}
    if d.get("__typename") != "DiscountCodeBasic" or not d["codes"]["nodes"]:
        return None
    value = d["customerGets"]["value"]
    if value["__typename"] == "DiscountAmount" and not value.get("appliesOnEachItem"):
        amount, pct = cents(value["amount"]["amount"]), None
    elif value["__typename"] == "DiscountPercentage":
        amount, pct = 0, round(float(value["percentage"]) * 100, 2)
    else:
        return None
    items = d["customerGets"]["items"]
    products = ([PREFIX + _numeric_id(p["id"]) for p in items["products"]["nodes"]]
                if items.get("__typename") == "DiscountProducts" else [])
    req = d.get("minimumRequirement") or {}
    minimum = cents(req["greaterThanOrEqualToSubtotal"]["amount"]) if req.get("__typename") == "DiscountMinimumSubtotal" else 0
    code = d["codes"]["nodes"][0]["code"].upper()
    oid = "sdc_" + _numeric_id(node["id"])
    label = f"{code}: " + (f"{fmt_cents(amount)} off" if pct is None else f"{pct:g}% off") + (f" orders over {fmt_cents(minimum)}" if minimum else "")
    return {"_id": oid, "offer_id": oid, "shopify_gid": node["id"], "source": "shopify", "merchant_id": MERCHANT_ID,
            "offer_type": "coupon", "code": code, "label": label, "amount_cents": amount, "percentage": pct,
            "minimum_spend_cents": minimum, "eligible_products": products, "applicable_payment_methods": [],
            "stacking_group": "merchant_coupon", "expires_at": d.get("endsAt"), "eligibility_price_basis": "merchandise_subtotal",
            "status": "active" if d["status"] == "ACTIVE" else d["status"].lower()}


def sync_discounts(store: BaseStore) -> int:
    seen, after = set(), None
    while True:
        page = api.gql(api.DISCOUNTS, {"after": after})["codeDiscountNodes"]
        for node in page["nodes"]:
            doc = map_discount(node)
            if doc:
                store.put("offers", doc)
                seen.add(doc["_id"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    for o in store.find("offers", {"source": "shopify"}):
        if o["_id"] not in seen:
            store.delete("offers", o["_id"])
    return len(seen)


# ---------------------------------------------------------------- totals from Shopify


def draft_input(*, variant_gid: str, quantity: int, shipping: dict, coupon_codes: list[str], purchase_id: str | None = None,
                quote_id: str | None = None, approved_charge_cents: int | None = None, customer_id: str | None = None,
                note: str | None = None, reserve_minutes: int = 0) -> dict:
    """The one cart description used to calculate, and later to create, the draft order, so the two can't drift."""
    out: dict = {
        "lineItems": [{"variantId": variant_gid, "quantity": quantity}],
        "shippingLine": {"title": shipping["label"], "priceWithCurrency": {"amount": api.money(shipping["price_cents"]), "currencyCode": "USD"}},
        "discountCodes": coupon_codes,
        "acceptAutomaticDiscounts": False,
    }
    if purchase_id:
        out["customAttributes"] = [{"key": "perkpilot_purchase_id", "value": purchase_id},
                                   {"key": "perkpilot_quote_id", "value": quote_id or ""},
                                   {"key": "perkpilot_approved_charge", "value": api.money(approved_charge_cents or 0)}]
        out["tags"] = ["perkpilot", "perkpilot-verified"]
        out["note"] = note or ""
    if customer_id:
        out["purchasingEntity"] = {"customerId": api.gid("Customer", customer_id)}
    if reserve_minutes:
        out["reserveInventoryUntil"] = (datetime.now(timezone.utc) + timedelta(minutes=reserve_minutes)).isoformat()
    return out


def _totals(node: dict) -> dict:
    return {"total_cents": _money_set(node, "totalPriceSet"), "tax_cents": _money_set(node, "totalTaxSet"),
            "subtotal_cents": _money_set(node, "subtotalPriceSet"), "shipping_cents": _money_set(node, "totalShippingPriceSet"),
            "discount_cents": _money_set(node, "totalDiscountsSet"), "taxes_included": bool(node.get("taxesIncluded"))}


_calc_cache: dict = {}
CALC_TTL_S = 120


def calculate(variant: dict, quantity: int, shipping: dict, coupon_codes: list[str]) -> dict | None:
    """Shopify's own numbers for this exact cart, or None when Shopify can't be asked (the verifier then fails closed)."""
    key = (variant.get("shopify_gid"), variant.get("price_cents"), quantity, shipping["id"], shipping["price_cents"], tuple(coupon_codes))
    hit = _calc_cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    if not variant.get("shopify_gid") or not api.configured():
        return None
    try:
        out = api.gql(api.DRAFT_CALCULATE, {"input": draft_input(variant_gid=variant["shopify_gid"], quantity=quantity,
                                                                  shipping=shipping, coupon_codes=coupon_codes)})["draftOrderCalculate"]
    except api.ShopifyError:
        return None
    if out["userErrors"] or not out["calculatedDraftOrder"]:
        return None
    res = {**_totals(out["calculatedDraftOrder"]), "source": "shopify_draft_order_calculate"}
    _calc_cache[key] = (time.time() + CALC_TTL_S, res)
    return res


def live_stock(variants: list[dict]) -> dict[str, int]:
    """Current sellable quantity per PerkPilot variant id, read from Shopify (not the synced projection)."""
    from .shopify import UNTRACKED_STOCK

    nodes = api.gql(api.VARIANTS, {"ids": [v["shopify_gid"] for v in variants]})["nodes"]
    by_gid = {n["id"]: n for n in nodes if n}
    out = {}
    for v in variants:
        n = by_gid.get(v["shopify_gid"])
        if not n:
            out[v["variant_id"]] = 0
        elif n["availableForSale"] and (not n["inventoryItem"]["tracked"] or n["inventoryPolicy"] == "CONTINUE"):
            out[v["variant_id"]] = UNTRACKED_STOCK
        else:
            out[v["variant_id"]] = max(n["inventoryQuantity"] or 0, 0)
    return out


# ---------------------------------------------------------------- checkout handoff


def _check(rule, label, ok, observed, required, explanation):
    from .verifier import FAIL, PASS, _check as make

    return make(rule, label, PASS if ok else FAIL, observed, required, explanation)


def begin_handoff(store: BaseStore, *, quote: dict, intent: dict, product: dict, variant: dict, verification: dict,
                  shopper: dict) -> dict:
    """Create the Shopify draft order for an approved, re-verified quote and return its checkout link.

    Shopify must agree with the approved charge to the cent. If its total differs (price edited in Shopify, a code
    that stopped applying), nothing is handed off and the shopper is asked to re-verify.
    """
    existing = store.find_one("handoffs", {"quote_id": quote["quote_id"]})
    if existing:
        return existing
    bd = verification["price_breakdown"]
    ship = next(o for o in product["delivery_options"] if o["id"] == quote["proposal"]["shipping_option"])
    purchase_id = next_id(store, "pur")
    note = (f"Verified by PerkPilot. The shopper approved {fmt_cents(bd['charge_cents'])} for {product['name']} "
            f"({variant['color'] or 'any color'} / {variant['size'] or 'one size'}), delivery by {verification['estimated_delivery_date']}. "
            f"Record {purchase_id}.")
    try:
        draft = api.mutate(api.DRAFT_CREATE, {"input": draft_input(
            variant_gid=variant["shopify_gid"], quantity=quote["proposal"]["quantity"], shipping=ship,
            coupon_codes=quote["proposal"]["coupon_codes"], purchase_id=purchase_id, quote_id=quote["quote_id"],
            approved_charge_cents=bd["charge_cents"], customer_id=shopper.get("shopify_customer_id"), note=note,
            reserve_minutes=30)}, "draftOrderCreate")["draftOrder"]
    except api.ShopifyError as exc:
        raise DomainError(f"Shopify couldn't create the checkout: {exc}", 502, "shopify_error")
    totals = _totals(draft)
    if totals["total_cents"] != bd["charge_cents"]:
        raise DomainError(f"Shopify now totals this cart at {fmt_cents(totals['total_cents'])}, not the {fmt_cents(bd['charge_cents'])} "
                          "you approved. Please re-verify.", 409, "NEEDS_RECONFIRMATION", {"shopify_total_cents": totals["total_cents"]})
    auth = append_event(store, subject_type="purchase", subject_id=purchase_id, purchase_id=purchase_id,
                        event_type="purchase.authorized_by_shopper", actor_type="shopper",
                        payload={"quote_id": quote["quote_id"], "quote_hash": quote["quote_hash"], "approved_charge_cents": bd["charge_cents"],
                                 "intent_version": intent["confirmation_version"], "shopify_draft_order": draft["name"]})
    append_event(store, subject_type="purchase", subject_id=purchase_id, purchase_id=purchase_id, event_type="checkout.handed_off",
                 actor_type="system", payload={"draft_order_id": draft["id"], "shopify_total_cents": totals["total_cents"],
                                               "inventory_reserved_until": draft.get("reserveInventoryUntil")})
    handoff = {
        "_id": purchase_id, "purchase_id": purchase_id, "quote_id": quote["quote_id"], "intent_id": intent["intent_id"],
        "shopper_id": quote["shopper_id"], "product_id": product["product_id"], "variant_id": variant["variant_id"],
        "quantity": quote["proposal"]["quantity"], "payment_method_id": quote["proposal"]["payment_method_id"],
        "breakdown": {**bd, "shopify_totals": totals}, "verification_checks": verification["checks"],
        "approved_delivery_date": verification["estimated_delivery_date"], "approved_at": now(store).isoformat(),
        "authorization_event_id": auth["event_id"], "draft_order_id": draft["id"], "draft_order_name": draft["name"],
        "checkout_url": draft["invoiceUrl"], "status": "awaiting_payment", "order_id": None,
        "shopify_customer_id": shopper.get("shopify_customer_id"),
    }
    store.put("handoffs", handoff)
    return handoff


def refresh_handoff(store: BaseStore, purchase_id: str) -> dict:
    """Ask Shopify whether the shopper paid. Works without webhooks, so a missed delivery can't strand an order."""
    h = store.get("handoffs", purchase_id)
    if not h:
        raise DomainError("Unknown checkout.", 404, "not_found")
    if h["status"] == "awaiting_payment":
        d = api.gql(api.DRAFT_STATUS, {"id": h["draft_order_id"]})["draftOrder"]
        if d and d.get("order"):
            reconcile_order(store, d["order"]["id"])
            h = store.get("handoffs", purchase_id)
    return h


# ---------------------------------------------------------------- reading orders back


SHIP_STATUS = {"DELIVERED": "delivered", "PICKED_UP": "delivered", "OUT_FOR_DELIVERY": "out_for_delivery",
               "ATTEMPTED_DELIVERY": "out_for_delivery", "IN_TRANSIT": "in_transit", "CARRIER_PICKED_UP": "in_transit",
               "DELAYED": "delayed"}
STEPS = ["label_created", "in_transit", "out_for_delivery", "delivered"]


def _vid(variant_gid: str | None, product_id: str) -> str | None:
    return f"{product_id}-{_numeric_id(variant_gid)}" if variant_gid else None


def _project_shipment(store: BaseStore, purchase: dict, order: dict) -> dict | None:
    fs = [f for f in order["fulfillments"] if f["status"] not in ("CANCELLED", "ERROR", "FAILURE")]
    if not fs:
        return None
    f = fs[0]
    status = "delivered" if f.get("deliveredAt") else SHIP_STATUS.get(f["displayStatus"] or "", "in_transit" if f.get("inTransitAt") else "label_created")
    reached = STEPS[:STEPS.index(status) + 1] if status in STEPS else ["label_created", "in_transit"]
    at = {"label_created": f["createdAt"], "in_transit": f.get("inTransitAt") or f["createdAt"],
          "out_for_delivery": f.get("deliveredAt") or f["createdAt"], "delivered": f.get("deliveredAt")}
    lines = f["fulfillmentLineItems"]["nodes"]
    delivered_vid = _vid(lines[0]["lineItem"]["variant"]["id"] if lines and lines[0]["lineItem"].get("variant") else None,
                         purchase["product_id"])
    tracking = (f.get("trackingInfo") or [{}])[0] if f.get("trackingInfo") else {}
    sid = "shp_" + purchase["purchase_id"]
    prev = store.get("shipments", sid)
    sh = {
        "_id": sid, "shipment_id": sid, "purchase_id": purchase["purchase_id"], "shopify_fulfillment_id": f["id"], "simulated": False,
        "carrier_fixture": tracking.get("company") or "Shopify fulfillment", "tracking_number": tracking.get("number"),
        "tracking_url": tracking.get("url"), "status": "delayed" if status == "delayed" else status,
        "events": [{"status": s, "at": at[s]} for s in reached if at.get(s)] + ([{"status": "delayed", "at": f["createdAt"]}] if status == "delayed" else []),
        "planned": [{"status": s, "at": None} for s in STEPS],
        "expected_delivery_date": (f.get("estimatedDeliveryAt") or "")[:10] or purchase["approved_delivery_date"],
        "actual_delivery_date": (datetime.fromisoformat(f["deliveredAt"].replace("Z", "+00:00")).astimezone(STORE_TZ).date().isoformat()
                                 if f.get("deliveredAt") else None),
        "delivered_at": f.get("deliveredAt"), "delivered_variant_id": delivered_vid if f.get("deliveredAt") else None,
        "fulfilled_variant_id": delivered_vid,
    }
    store.put("shipments", sh)
    seen = {e["status"] for e in (prev or {}).get("events", [])}
    for e in sh["events"]:
        if e["status"] not in seen:
            payload = {"status": e["status"], "shopify_fulfillment_id": f["id"], "carrier": sh["carrier_fixture"]}
            if e["status"] == "delivered":
                payload["delivered_sku_matches_confirmed"] = delivered_vid == purchase["variant_id"]
            append_event(store, subject_type="purchase", subject_id=purchase["purchase_id"], purchase_id=purchase["purchase_id"],
                         event_type=f"shipment.{e['status']}", actor_type="shopify_fulfillment", payload=payload)
    return sh


def _card(order: dict) -> dict:
    tx = next((t for t in order["transactions"] if t["kind"] in ("SALE", "AUTHORIZATION", "CAPTURE") and t["status"] == "SUCCESS"), None)
    details = (tx or {}).get("paymentDetails") or {}
    number = details.get("number") or ""
    return {"gateway": (tx or {}).get("gateway"), "company": details.get("company"), "last4": number[-4:] if number else None,
            "test": bool((tx or {}).get("test"))}


def _create_record(store: BaseStore, h: dict, order: dict) -> dict:
    from .verifier import FAIL

    product = store.get("products", h["product_id"])
    variant = next(v for v in product["variants"] if v["variant_id"] == h["variant_id"])
    intent = store.get("intents", h["intent_id"])
    total = _money_set(order, "currentTotalPriceSet")
    card = _card(order)
    checks = [c for c in h["verification_checks"]] + [
        _check("ORDER_TOTAL_MATCHES_APPROVAL", "Shopify order total matches your approval", total == h["breakdown"]["charge_cents"],
               fmt_cents(total), fmt_cents(h["breakdown"]["charge_cents"]),
               "Shopify charged exactly what you approved." if total == h["breakdown"]["charge_cents"] else
               "The Shopify order total differs from the amount you approved."),
    ]
    if h["breakdown"]["later_statement_credit_cents"]:
        visa = (card["company"] or "").lower() == "visa"
        checks.append(_check("CARD_OFFER_PAYMENT", "Card-linked offer paid with an eligible card", visa,
                             card["company"] or "unknown card", "Visa",
                             "Paid with Visa, so the statement credit can post." if visa else "The order wasn't paid with Visa, so no credit applies."))
        checks[-1]["blocking"] = False
    label = " ".join(x for x in [card["company"], f"•••• {card['last4']}" if card["last4"] else None] if x) or "Paid on Shopify checkout"
    record = make_purchase_record(
        store, purchase_id=h["purchase_id"], shopper_id=h["shopper_id"], product=product, variant=variant, quantity=h["quantity"],
        breakdown=h["breakdown"], payment_method_id=h["payment_method_id"], approved_delivery_date=date.fromisoformat(h["approved_delivery_date"]),
        verification_checks=checks, simulated_transaction_id=f"Shopify order {order['name']}", created_at=datetime.fromisoformat(h["approved_at"]),
        intent=intent, authorization_event_id=h["authorization_event_id"],
        payment_reference={"id": h["payment_method_id"], "label": label + (" (Shopify test payment)" if order["test"] else ""),
                           "simulated": False, "provider": "shopify", **card})
    record.update(shopify_order_id=order["id"], shopify_order_name=order["name"], shopify_test=bool(order["test"]),
                  quote_id=h["quote_id"], shopify_customer_id=h.get("shopify_customer_id"), shopify_observed=[])
    store.put("purchases", record)
    append_event(store, subject_type="purchase", subject_id=record["purchase_id"], purchase_id=record["purchase_id"],
                 event_type="purchase.confirmed", actor_type="system",
                 payload={"record_hash": record["record_hash"], "shopify_order": order["name"], "approved_delivery_date": record["approved_delivery_date"],
                          "total_matches_approval": not any(c["status"] == FAIL and c["rule"] == "ORDER_TOTAL_MATCHES_APPROVAL" for c in checks)})
    quote = store.get("quotes", h["quote_id"])
    store.put("quotes", {**quote, "status": "used", "purchase_id": h["purchase_id"]})
    store.put("intents", {**intent, "status": "purchased"})
    store.put("handoffs", {**h, "status": "paid", "order_id": order["id"], "order_name": order["name"]})
    return record


def _observe(store: BaseStore, purchase: dict, key: str, event_type: str, payload: dict) -> None:
    if key in purchase.setdefault("shopify_observed", []):
        return
    purchase["shopify_observed"].append(key)
    append_event(store, subject_type="purchase", subject_id=purchase["purchase_id"], purchase_id=purchase["purchase_id"],
                 event_type=event_type, actor_type="shopify", payload={**payload, "simulated": False})


def reconcile_order(store: BaseStore, order_gid: str) -> dict | None:
    """Read the canonical order from Shopify and fold it into PerkPilot's record. Safe to repeat and to run out of order."""
    from .shopify_stream import order_from_graphql

    o = api.gql(api.ORDER, {"id": order_gid})["order"]
    if not o:
        return None
    store.put("shopify_orders", order_from_graphql(o))
    attrs = {a["key"]: a["value"] for a in o["customAttributes"]}
    pid = attrs.get("perkpilot_purchase_id")
    if not pid:
        return None
    purchase = store.get("purchases", pid)
    if not purchase:
        h = store.get("handoffs", pid)
        if not h:
            return None
        purchase = _create_record(store, h, o)
    elif not purchase.get("shopify_order_id"):
        purchase.update(shopify_order_id=o["id"], shopify_order_name=o["name"], shopify_test=bool(o["test"]))
    total, received, refunded = (_money_set(o, "currentTotalPriceSet"), _money_set(o, "totalReceivedSet"), _money_set(o, "totalRefundedSet"))
    card = _card(o)
    if o["displayFinancialStatus"] in ("AUTHORIZED", "PAID", "PARTIALLY_PAID", "PARTIALLY_REFUNDED", "REFUNDED"):
        _observe(store, purchase, "payment.authorized", "payment.authorized",
                 {"amount_cents": total, "network": card["company"], "last4": card["last4"], "gateway": card["gateway"],
                  "test": bool(o["test"]), "shopify_order": o["name"]})
    if received:
        _observe(store, purchase, f"payment.settled:{received}", "payment.settled", {"amount_cents": received, "shopify_order": o["name"]})
    for r in o["refunds"]:
        _observe(store, purchase, f"refund:{r['id']}", "refund.observed",
                 {"amount_cents": _money_set(r, "totalRefundedSet"), "refund_id": r["id"], "note": r.get("note")})
    if refunded and purchase["status"] in ("confirmed", "shipped", "delivered"):
        purchase["status"] = "refunded" if refunded >= total else "partially_refunded"
    sh = _project_shipment(store, purchase, o)
    if sh and purchase["status"] in ("confirmed", "shipped"):
        purchase["status"] = "delivered" if sh["status"] == "delivered" else "shipped"
    purchase["shopify_financial_status"] = o["displayFinancialStatus"].lower()
    purchase["shopify_fulfillment_status"] = o["displayFulfillmentStatus"].lower()
    purchase["shopify_synced_at"] = datetime.now(timezone.utc).isoformat()
    store.put("purchases", purchase)
    return purchase


def maybe_refresh(store: BaseStore, purchase: dict, max_age_s: int = 15) -> dict:
    """Re-read a Shopify-backed purchase when a shopper or merchant looks at it, so the view never waits on a webhook."""
    if not purchase.get("shopify_order_id") or not api.configured():
        return purchase
    synced = purchase.get("shopify_synced_at")
    if synced and (datetime.now(timezone.utc) - datetime.fromisoformat(synced)).total_seconds() < max_age_s:
        return purchase
    try:
        return reconcile_order(store, purchase["shopify_order_id"]) or purchase
    except api.ShopifyError:
        return purchase


# ---------------------------------------------------------------- remedies executed in Shopify


def can_auto_execute(store: BaseStore, purchase: dict) -> bool:
    """Only Shopify test orders run without a person; a real order's money always waits for the merchant."""
    return bool(purchase.get("shopify_test")) and store.get("policies", POLICY_ID).get("auto_execute_test_remedies", True)


def refund(store: BaseStore, purchase: dict, remedy: dict) -> dict:
    """refundCreate for one accepted remedy. The remedy id goes in the refund note so a retry finds it instead of refunding twice."""
    order_gid = purchase["shopify_order_id"]
    marker = f"PerkPilot remedy {remedy['remedy_id']}"
    o = api.gql(api.ORDER, {"id": order_gid})["order"]
    done = next((r for r in o["refunds"] if marker in (r.get("note") or "")), None)
    if done:
        return {"refund_id": done["id"], "amount_cents": _money_set(done, "totalRefundedSet"), "duplicate": True}
    note = f"{marker}: {remedy['title']} (claim {remedy['claim_id']})."
    rtype = remedy["remedy_type"]
    inp: dict = {"orderId": order_gid, "note": note, "notify": False}
    if rtype == "store_credit":
        inp["refundMethods"] = [{"storeCreditRefund": {"amount": {"amount": api.money(remedy["amount_cents"]), "currencyCode": "USD"}}}]
    elif rtype == "refund":
        lines = [{"lineItemId": li["id"], "quantity": li["currentQuantity"], "restockType": "NO_RESTOCK"}
                 for li in o["lineItems"]["nodes"] if li["currentQuantity"]]
        sug = api.gql(api.SUGGESTED_REFUND, {"id": order_gid, "lines": lines})["order"]["suggestedRefund"]
        # Never refund more than the remedy the shopper accepted (e.g. a policy return fee is kept).
        left, txs = remedy["amount_cents"], []
        for t in sug["suggestedTransactions"]:
            amount = min(cents(t["amountSet"]["shopMoney"]["amount"]), left)
            left -= amount
            if amount:
                txs.append({"orderId": order_gid, "gateway": t["gateway"], "kind": "REFUND", "amount": api.money(amount),
                            "parentId": t["parentTransaction"]["id"] if t.get("parentTransaction") else None})
        inp.update(refundLineItems=lines, shipping={"fullRefund": True}, transactions=txs)
        if remedy["amount_cents"] < cents(sug["amountSet"]["shopMoney"]["amount"]):
            inp["discrepancyReason"] = "CUSTOMER"
    else:  # partial_refund, keep_item_partial_refund: money only, the item stays with the shopper
        sale = next((t for t in o["transactions"] if t["kind"] in ("SALE", "CAPTURE") and t["status"] == "SUCCESS"), None)
        if not sale:
            raise DomainError("Shopify shows no captured payment to refund yet.", 409, "not_refundable")
        inp["transactions"] = [{"orderId": order_gid, "gateway": sale["gateway"], "kind": "REFUND",
                                "amount": api.money(remedy["amount_cents"]), "parentId": sale["id"]}]
    r = api.mutate(api.REFUND_CREATE, {"input": inp}, "refundCreate")["refund"]
    return {"refund_id": r["id"], "amount_cents": _money_set(r, "totalRefundedSet"), "duplicate": False}


def create_replacement(store: BaseStore, purchase: dict, target: dict, exchange_purchase_id: str, claim_id: str) -> dict:
    """A free replacement as a real Shopify order: draft order at 100% off with free shipping, completed as paid.
    Shopify allocates the inventory and the merchant fulfils it like any other order."""
    inp = {
        "lineItems": [{"variantId": target["shopify_gid"], "quantity": purchase["quantity"]}],
        "appliedDiscount": {"title": "PerkPilot exchange", "description": f"Replacement for {purchase.get('shopify_order_name')}",
                            "value": 100.0, "valueType": "PERCENTAGE"},
        "shippingLine": {"title": "Exchange replacement (free)", "priceWithCurrency": {"amount": "0.00", "currencyCode": "USD"}},
        "customAttributes": [{"key": "perkpilot_purchase_id", "value": exchange_purchase_id},
                             {"key": "perkpilot_exchange_for", "value": purchase.get("shopify_order_name") or purchase["purchase_id"]},
                             {"key": "perkpilot_claim_id", "value": claim_id}],
        "tags": ["perkpilot", "perkpilot-exchange"],
        "note": f"PerkPilot exchange for {purchase.get('shopify_order_name')} (claim {claim_id}): {target['color'] or ''} {target['size'] or ''}".strip(),
    }
    if purchase.get("shopify_customer_id"):
        inp["purchasingEntity"] = {"customerId": api.gid("Customer", purchase["shopify_customer_id"])}
    draft = api.mutate(api.DRAFT_CREATE, {"input": inp}, "draftOrderCreate")["draftOrder"]
    done = api.mutate(api.DRAFT_COMPLETE, {"id": draft["id"]}, "draftOrderComplete")["draftOrder"]
    return {"order_id": done["order"]["id"], "order_name": done["order"]["name"], "draft_order_id": draft["id"]}


def open_return(purchase: dict, claim: dict) -> dict | None:
    """Best effort: a Shopify return for the original item, so it shows in the merchant's returns workflow."""
    try:
        o = api.gql(api.ORDER, {"id": purchase["shopify_order_id"]})["order"]
        lines = [n for f in o["fulfillments"] for n in f["fulfillmentLineItems"]["nodes"]]
        if not lines:
            return None
        out = api.mutate(api.RETURN_CREATE, {"input": {"orderId": o["id"], "returnLineItems": [
            {"fulfillmentLineItemId": lines[0]["id"], "quantity": lines[0]["quantity"],
             "returnReasonNote": f"PerkPilot claim {claim['claim_id']}: {claim['structured_issue']['problem_type'].replace('_', ' ')}"}]}},
            "returnCreate")["return"]
        return {"return_id": out["id"], "return_name": out["name"]}
    except (api.ShopifyError, KeyError, TypeError):
        return None


def tag_order(order_gid: str | None, tags: list[str]) -> None:
    if order_gid:
        try:
            api.mutate(api.TAGS_ADD, {"id": order_gid, "tags": tags}, "tagsAdd")
        except api.ShopifyError:
            pass  # a tag is a convenience for the merchant, never a reason to fail the shopper's remedy


# ---------------------------------------------------------------- test-store helper: ship and deliver


def ship_and_deliver(store: BaseStore, purchase: dict) -> dict:
    """For Shopify test orders only: fulfil the open fulfillment orders and record a DELIVERED fulfillment event, the
    same events a carrier integration would post. Real orders are fulfilled by the merchant's normal process."""
    if not purchase.get("shopify_test"):
        raise DomainError("Only Shopify test orders can be shipped from PerkPilot. Fulfil real orders in Shopify.", 409, "not_test_order")
    o = api.gql(api.ORDER, {"id": purchase["shopify_order_id"]})["order"]
    open_fos = [fo for fo in o["fulfillmentOrders"]["nodes"] if fo["status"] in ("OPEN", "IN_PROGRESS")
                and any(li["remainingQuantity"] for li in fo["lineItems"]["nodes"])]
    for fo in open_fos:
        api.mutate(api.FULFILLMENT_CREATE, {"fulfillment": {
            "lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": fo["id"]}], "notifyCustomer": False,
            "trackingInfo": {"company": TEST_CARRIER, "number": f"PP{_numeric_id(o['id'])}"}}}, "fulfillmentCreate")
    if open_fos:
        o = api.gql(api.ORDER, {"id": purchase["shopify_order_id"]})["order"]
    for f in o["fulfillments"]:
        if not f.get("deliveredAt") and f["status"] == "SUCCESS":
            api.mutate(api.FULFILLMENT_EVENT_CREATE, {"event": {"fulfillmentId": f["id"], "status": "DELIVERED",
                                                                "happenedAt": datetime.now(timezone.utc).isoformat(),
                                                                "message": "Delivered (PerkPilot test-store helper)"}}, "fulfillmentEventCreate")
    return reconcile_order(store, purchase["shopify_order_id"])


# ---------------------------------------------------------------- approved guidance on the storefront


METAFIELD_NS, METAFIELD_KEY = "perkpilot", "fit_guidance"


def guidance_for_product(store: BaseStore, product_id: str) -> dict:
    """{size: {suggest_size, message, evidence}} for every approved, enabled rule on this product."""
    out = {}
    for r in store.find("rules", {"product_scope": product_id, "status": "approved"}):
        if r.get("enabled", True):
            out[r["variant_scope"]["size"]] = {"suggest_size": r["proposed_action"]["suggest_size"], "message": r["customer_message"],
                                               "evidence": f"{r['confirmed_outcome_count']} confirmed fit exchanges among "
                                                           f"{r['affected_order_count']} fulfilled orders",
                                               "rule_id": r["rule_id"]}
    return out


def sync_guidance(store: BaseStore, product_id: str) -> dict:
    """Mirror approved guidance into the product's metafield so the theme renders it even if PerkPilot is unreachable."""
    product = store.get("products", product_id)
    if not product or product.get("source") != "shopify" or not api.configured():
        return {"synced": False, "reason": "not a Shopify product"}
    data = guidance_for_product(store, product_id)
    try:
        if data:
            api.mutate(api.METAFIELDS_SET, {"metafields": [{"ownerId": product["shopify_gid"], "namespace": METAFIELD_NS,
                                                            "key": METAFIELD_KEY, "type": "json", "value": json.dumps(data)}]}, "metafieldsSet")
        else:
            api.mutate(api.METAFIELDS_DELETE, {"metafields": [{"ownerId": product["shopify_gid"], "namespace": METAFIELD_NS,
                                                               "key": METAFIELD_KEY}]}, "metafieldsDelete")
    except api.ShopifyError as exc:
        return {"synced": False, "reason": str(exc)}
    return {"synced": True, "sizes": sorted(data), "at": datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------- pilot setup: the judge demo on a real store


def create_discount(code: str, amount_cents: int, minimum_cents: int) -> str:
    out = api.mutate(api.DISCOUNT_CREATE, {"discount": {
        "title": f"{code} (PerkPilot demo)", "code": code, "startsAt": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        "customerGets": {"value": {"discountAmount": {"amount": api.money(amount_cents), "appliesOnEachItem": False}}, "items": {"all": True}},
        "minimumRequirement": {"subtotal": {"greaterThanOrEqualToSubtotal": api.money(minimum_cents)}},
        "combinesWith": {"orderDiscounts": False, "productDiscounts": False, "shippingDiscounts": False}}}, "discountCodeBasicCreate")
    return out["codeDiscountNode"]["id"]


def _weekday_after(d: date) -> str:
    return f"{d:%A}"


def prepare_scenario(store: BaseStore, product_id: str, variant_id: str) -> dict:
    """Tune the coupon, the card-linked offer and a budget so the AI agent's classic mistake shows up on a real product:
    with the coupon the charge drops below the card offer's threshold, so the credit the agent claims doesn't exist,
    and skipping the coupon would exceed the shopper's budget. Every number is computed from Shopify's totals."""
    product = store.get("products", product_id)
    variant = next((v for v in (product or {}).get("variants", []) if v["variant_id"] == variant_id), None)
    if not variant or product.get("source") != "shopify":
        raise DomainError("Pick a synced Shopify product variant.", 422, "invalid_request")
    pol = store.get("policies", POLICY_ID)
    ship = next(o for o in pol["shipping_options"] if o["id"] == "standard")
    full = calculate(variant, 1, ship, [])
    if not full:
        raise DomainError("Shopify couldn't price this cart. Check the app's write_draft_orders scope.", 502, "shopify_error")
    price = variant["price_cents"]
    coupon = max(5_00, round(price * 0.15 / 100) * 100)
    code = f"PERKPILOT{coupon // 100}"
    if not any((o.get("code") or "") == code for o in store.find("offers")):
        create_discount(code, coupon, max(price - 1_00, 1_00))
    sync_discounts(store)
    with_coupon = calculate(variant, 1, ship, [code])
    if not with_coupon:
        raise DomainError("Shopify didn't apply the new code yet; try again in a few seconds.", 409, "retry")
    # Paid in full the charge qualifies; with the coupon it doesn't. Item + shipping (pre-tax) keeps the threshold within
    # reach of the agent's flawed pre-discount arithmetic, which is exactly the mistake the verifier must catch.
    threshold = min(full["total_cents"], price + ship["price_cents"])
    if threshold <= with_coupon["total_cents"]:
        raise DomainError("This item's tax is too high for the coupon to cross the card-offer threshold; pick another product.", 422,
                          "scenario_not_possible")
    credit = min(10_00, max(1_00, (coupon // 2) // 100 * 100))
    budget = ((with_coupon["total_cents"] + 4_99) // 5_00) * 5_00  # next $5 above the coupon charge
    if budget >= full["total_cents"]:
        budget = with_coupon["total_cents"]
    update_settings(store, {"card_offer": {"enabled": True, "credit_cents": credit, "threshold_cents": threshold}})
    from .pricing import estimate_delivery

    arrives = estimate_delivery(store, ship)
    words = " ".join(w for w in [variant["color"], product["category"].replace("_", " "), f"in size {variant['size']}" if variant["size"] else ""] if w)
    return {"coupon_code": code, "coupon_cents": coupon, "card_offer_threshold_cents": threshold, "card_offer_credit_cents": credit,
            "charge_without_coupon_cents": full["total_cents"], "charge_with_coupon_cents": with_coupon["total_cents"],
            "budget_cents": budget, "deliver_by": arrives.isoformat(),
            "shopper_prompt": f"Find a {words} under ${budget / 100:.0f} including shipping and tax, delivered by {_weekday_after(arrives)}."}


def seed_fit_history(store: BaseStore, product_id: str, size: str, to_size: str, orders: int = 20, validated: int = 2) -> dict:
    """Labeled synthetic history for one Shopify product, so the learning loop can be shown on a new store. Every record
    is source="seed", appears as seeded wherever it's shown, and is never mixed into session metrics."""
    from .verifier import PASS, _check as make

    product = store.get("products", product_id)
    variant = next((v for v in (product or {}).get("variants", []) if v["size"] == size), None)
    target = next((v for v in (product or {}).get("variants", []) if v["size"] == to_size and v["color"] == (variant or {}).get("color")), None)
    if not variant or not target:
        raise DomainError(f"{(product or {}).get('name', 'That product')} needs both size {size} and size {to_size}.", 422, "invalid_request")
    for c in store.find("claims", {"source": "seed"}):
        if (store.get("purchases", c["purchase_id"]) or {}).get("product_id") == product_id:
            raise DomainError("Sample history for this product is already loaded.", 409, "already_seeded")
    base = now(store) - timedelta(days=45)
    bd = {"currency": "USD", "unit_price_cents": variant["price_cents"], "quantity": 1, "merchandise_cents": variant["price_cents"],
          "shipping_cents": 0, "shipping_label": None, "tax_cents": 0, "coupon_discount_cents": 0, "charge_cents": variant["price_cents"],
          "later_statement_credit_cents": 0, "effective_cost_cents": variant["price_cents"], "coupon_evaluations": [], "card_offer_evaluations": []}
    made = []
    for i in range(orders):
        placed = base + timedelta(days=i)
        pid = next_id(store, "seedpur")
        rec = make_purchase_record(store, purchase_id=pid, shopper_id=f"seed_shopper_{i:02d}", product=product, variant=variant, quantity=1,
                                   breakdown=bd, payment_method_id=PM_VISA, approved_delivery_date=add_business_days(placed.date(), 3),
                                   verification_checks=[make("SEEDED_HISTORY", "Seeded history", PASS, explanation="Labeled synthetic order.")],
                                   simulated_transaction_id="seeded", created_at=placed, source="seed")
        store.put("purchases", rec)
        delivered = datetime.combine(add_business_days(placed.date(), 3), datetime.min.time(), tzinfo=STORE_TZ).replace(hour=14)
        store.put("shipments", {"_id": f"shp_{pid}", "shipment_id": f"shp_{pid}", "purchase_id": pid, "carrier_fixture": "seeded",
                                "status": "delivered", "events": [{"status": "delivered", "at": delivered.isoformat()}], "planned": [],
                                "expected_delivery_date": rec["approved_delivery_date"], "actual_delivery_date": delivered.date().isoformat(),
                                "delivered_at": delivered.isoformat(), "delivered_variant_id": variant["variant_id"], "simulated": True})
        made.append(rec)
    for j in range(validated):
        p = made[3 + j * 7]
        at = datetime.fromisoformat(p["created_at"]) + timedelta(days=6)
        cid = next_id(store, "seedclm")
        store.put("claims", {
            "_id": cid, "claim_id": cid, "purchase_id": p["purchase_id"], "shopper_id": p["shopper_id"], "merchant_id": MERCHANT_ID,
            "complaint_text": f"Size {size} runs small for me.", "clarifications": [],
            "structured_issue": {"problem_type": "fit_tight", "details": "runs small", "requested_outcome": "exchange",
                                 "mentioned_size": None, "mentioned_color": None, "missing_information": []},
            "interpreter": {"mode": "seed", "note": "Labeled synthetic claim."}, "classification": "SUSPECTED_PRODUCT_FIT",
            "classification_reason": "Seeded: delivered size matched the order; shopper reported a tight fit.", "evidence_references": [],
            "evidence_status": "confirmed_outcome", "requires_review": False, "responsibility": "merchant_goodwill_policy",
            "status": "resolved", "accepted_remedy_id": None, "follow_up_question": None, "source": "seed", "created_at": at.isoformat(),
            "validated_outcome": {"type": "fit_exchange_confirmed", "product_id": product_id, "from_size": size, "to_size": to_size,
                                  "replacement_variant_id": target["variant_id"], "confirmed_at": (at + timedelta(days=6)).isoformat()},
            "history": []})
        store.put("purchases", {**p, "status": "exchanged"})
    return {"orders": orders, "validated_outcomes": validated, "product": product["name"], "size": size, "to_size": to_size,
            "note": f"Labeled synthetic history: {orders} fulfilled size-{size} orders and {validated} confirmed exchanges to size {to_size}. "
                    "One more real confirmed exchange crosses the proposal threshold."}


