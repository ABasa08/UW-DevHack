"""Shopify Admin GraphQL client and every document PerkPilot sends.

All documents are module-level constants so ``scripts/check_shopify_queries.py`` can validate them
against Shopify's published schema for the pinned API version.
"""

from __future__ import annotations

import os
import time

import httpx

from .shopify import API_VERSION, get_token


class ShopifyError(RuntimeError):
    """A Shopify request failed, or a mutation returned userErrors."""


def shop_domain() -> str:
    return os.environ.get("SHOPIFY_SHOP", "").removeprefix("https://").strip("/")


def configured() -> bool:
    return bool(shop_domain() and (os.environ.get("SHOPIFY_ADMIN_TOKEN") or os.environ.get("SHOPIFY_CLIENT_SECRET")))


def api_version() -> str:
    return os.environ.get("SHOPIFY_API_VERSION", API_VERSION)


_token: dict = {}
_transport: httpx.BaseTransport | None = None  # tests swap in a fake Shopify


def cached_token(client: httpx.Client, shop: str) -> str:
    """Client-credentials tokens live 24h; reuse one for 23h instead of exchanging on every call."""
    if _token.get("shop") == shop and _token.get("expires", 0) > time.time():
        return _token["value"]
    value = get_token(client, shop)
    _token.update(shop=shop, value=value, expires=time.time() + 23 * 3600)
    return value


def gql(query: str, variables: dict | None = None, client: httpx.Client | None = None, retries: int = 2) -> dict:
    """POST one document; retry briefly when Shopify throttles. Raises ShopifyError on transport or GraphQL errors."""
    shop = shop_domain()
    if not configured():
        raise ShopifyError("Shopify isn't configured (SHOPIFY_SHOP plus app credentials).")
    own = client is None
    c = client or httpx.Client(timeout=30, transport=_transport)
    try:
        for attempt in range(retries + 1):
            try:
                r = c.post(f"https://{shop}/admin/api/{api_version()}/graphql.json", json={"query": query, "variables": variables or {}},
                           headers={"X-Shopify-Access-Token": cached_token(c, shop)}, timeout=30)
            except httpx.HTTPError as exc:
                raise ShopifyError(f"Shopify request failed: {exc}") from exc
            if r.status_code == 401:
                _token.clear()  # expired or revoked; the next call exchanges again
            throttled = r.status_code == 429 or (r.status_code == 200 and any(
                (e.get("extensions") or {}).get("code") == "THROTTLED" for e in r.json().get("errors") or []))
            if throttled and attempt < retries:
                time.sleep(1.0 + attempt)
                continue
            if r.status_code >= 400:
                raise ShopifyError(f"Shopify returned HTTP {r.status_code}: {r.text[:300]}")
            body = r.json()
            if body.get("errors"):
                raise ShopifyError(f"Shopify GraphQL error: {body['errors']}")
            return body["data"]
        raise ShopifyError("Shopify kept throttling the request.")
    finally:
        if own:
            c.close()


def mutate(query: str, variables: dict, root: str, client: httpx.Client | None = None) -> dict:
    """Run a mutation and fail on userErrors, so a refused write is never mistaken for success."""
    out = gql(query, variables, client)[root]
    errors = out.get("userErrors") or []
    if errors:
        raise ShopifyError("; ".join(e["message"] for e in errors))
    return out


def gid(kind: str, numeric: str | int) -> str:
    return f"gid://shopify/{kind}/{numeric}"


def money(cents: int) -> str:
    return f"{cents / 100:.2f}"


# ---------------------------------------------------------------- documents

SHOP = """
query PerkPilotShop { shop { name currencyCode myshopifyDomain } }
"""

_TOTALS = """
  totalPriceSet { shopMoney { amount currencyCode } }
  totalTaxSet { shopMoney { amount } }
  subtotalPriceSet { shopMoney { amount } }
  totalShippingPriceSet { shopMoney { amount } }
  totalDiscountsSet { shopMoney { amount } }
"""

DRAFT_CALCULATE = """
mutation PerkPilotCalculate($input: DraftOrderInput!) {
  draftOrderCalculate(input: $input) {
    calculatedDraftOrder { taxesIncluded %s }
    userErrors { field message }
  }
}
""" % _TOTALS

DRAFT_CREATE = """
mutation PerkPilotDraft($input: DraftOrderInput!) {
  draftOrderCreate(input: $input) {
    draftOrder { id name invoiceUrl status reserveInventoryUntil %s }
    userErrors { field message }
  }
}
""" % _TOTALS

DRAFT_STATUS = """
query PerkPilotDraftStatus($id: ID!) {
  draftOrder(id: $id) { id status order { id name } }
}
"""

DRAFT_COMPLETE = """
mutation PerkPilotComplete($id: ID!) {
  draftOrderComplete(id: $id) {
    draftOrder { id status order { id name } }
    userErrors { field message }
  }
}
"""

ORDER = """
query PerkPilotOrder($id: ID!) {
  order(id: $id) {
    id name test createdAt processedAt cancelledAt tags
    displayFinancialStatus displayFulfillmentStatus
    currentTotalPriceSet { shopMoney { amount currencyCode } }
    totalReceivedSet { shopMoney { amount } }
    totalRefundedSet { shopMoney { amount } }
    customAttributes { key value }
    lineItems(first: 20) { nodes { id title variantTitle quantity currentQuantity product { id } variant { id } originalUnitPriceSet { shopMoney { amount } } } }
    transactions(first: 20) {
      id kind status gateway test processedAt
      amountSet { shopMoney { amount } }
      paymentDetails { ... on CardPaymentDetails { company number } }
    }
    fulfillments(first: 10) {
      id status displayStatus createdAt inTransitAt deliveredAt estimatedDeliveryAt
      trackingInfo(first: 1) { company number url }
      fulfillmentLineItems(first: 20) { nodes { id quantity lineItem { id variant { id } } } }
    }
    refunds(first: 20) { id note createdAt totalRefundedSet { shopMoney { amount } } }
    fulfillmentOrders(first: 10) { nodes { id status lineItems(first: 20) { nodes { id remainingQuantity } } } }
  }
}
"""

VARIANTS = """
query PerkPilotVariants($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on ProductVariant { id price inventoryQuantity availableForSale inventoryPolicy inventoryItem { tracked } }
  }
}
"""

SUGGESTED_REFUND = """
query PerkPilotSuggestedRefund($id: ID!, $lines: [RefundLineItemInput!]) {
  order(id: $id) {
    suggestedRefund(refundLineItems: $lines, refundShipping: true) {
      amountSet { shopMoney { amount } }
      suggestedTransactions { gateway kind parentTransaction { id } amountSet { shopMoney { amount } } }
    }
  }
}
"""

REFUND_CREATE = """
mutation PerkPilotRefund($input: RefundInput!) {
  refundCreate(input: $input) {
    refund { id totalRefundedSet { shopMoney { amount } } }
    userErrors { field message }
  }
}
"""

RETURN_CREATE = """
mutation PerkPilotReturn($input: ReturnInput!) {
  returnCreate(returnInput: $input) {
    return { id name status }
    userErrors { field message }
  }
}
"""

TAGS_ADD = """
mutation PerkPilotTags($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) { userErrors { field message } }
}
"""

FULFILLMENT_CREATE = """
mutation PerkPilotFulfill($fulfillment: FulfillmentInput!) {
  fulfillmentCreate(fulfillment: $fulfillment) {
    fulfillment { id status }
    userErrors { field message }
  }
}
"""

FULFILLMENT_EVENT_CREATE = """
mutation PerkPilotFulfillmentEvent($event: FulfillmentEventInput!) {
  fulfillmentEventCreate(fulfillmentEvent: $event) {
    fulfillmentEvent { id status }
    userErrors { field message }
  }
}
"""

METAFIELDS_SET = """
mutation PerkPilotGuidance($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    metafields { id }
    userErrors { field message }
  }
}
"""

METAFIELDS_DELETE = """
mutation PerkPilotGuidanceDelete($metafields: [MetafieldIdentifierInput!]!) {
  metafieldsDelete(metafields: $metafields) { userErrors { field message } }
}
"""

DISCOUNTS = """
query PerkPilotDiscounts($after: String) {
  codeDiscountNodes(first: 50, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      codeDiscount {
        __typename
        ... on DiscountCodeBasic {
          title status endsAt
          codes(first: 5) { nodes { code } }
          minimumRequirement {
            __typename
            ... on DiscountMinimumSubtotal { greaterThanOrEqualToSubtotal { amount } }
          }
          customerGets {
            value {
              __typename
              ... on DiscountAmount { amount { amount } appliesOnEachItem }
              ... on DiscountPercentage { percentage }
            }
            items {
              __typename
              ... on DiscountProducts { products(first: 100) { nodes { id } } }
            }
          }
        }
      }
    }
  }
}
"""

DISCOUNT_CREATE = """
mutation PerkPilotDiscount($discount: DiscountCodeBasicInput!) {
  discountCodeBasicCreate(basicCodeDiscount: $discount) {
    codeDiscountNode { id }
    userErrors { field message }
  }
}
"""

DOCUMENTS = {k: v for k, v in dict(globals()).items() if k.isupper() and isinstance(v, str) and ("query" in v or "mutation" in v)}
