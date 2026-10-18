"""A small in-memory Shopify Admin GraphQL server for Shopify-mode tests.

It answers the documents in app/shopify_api.py by operation name and keeps enough state (variants, discount codes,
draft orders, orders, transactions, fulfillments, refunds, metafields) to run the whole judge flow end to end:
quote -> draft order -> paid order -> fulfillment -> claim -> refund/exchange -> learned guidance on the product.
"""

from __future__ import annotations

import atexit
import json
import os
import re
from decimal import Decimal

import httpx

TAX_RATE = Decimal("0.05")


def d(cents: int) -> str:
    return f"{cents / 100:.2f}"


def money_set(cents: int) -> dict:
    return {"shopMoney": {"amount": d(cents), "currencyCode": "USD"}}


def c(amount) -> int:
    return int(Decimal(str(amount)) * 100)


RECORDED: list = []
if os.environ.get("PERKPILOT_RECORD_SHOPIFY_CALLS"):
    atexit.register(lambda: open(os.environ["PERKPILOT_RECORD_SHOPIFY_CALLS"], "w").write(json.dumps(RECORDED)))


class FakeShopify:
    def __init__(self):
        self.variants = {}          # gid -> {price_cents, qty}
        self.codes = {}             # CODE -> {id, amount_cents, min_cents}
        self.drafts, self.orders = {}, {}
        self.metafields = {}        # owner gid -> value
        self.variant_product = {}   # variant gid -> product numeric id
        self.calls: list[tuple[str, dict]] = []
        self.n = 1000
        self.charge_extra_cents = 0  # simulate tax changing at checkout (address entered)

    def _id(self, kind):
        self.n += 1
        return f"gid://shopify/{kind}/{self.n}"

    # ------------------------------------------------------------ catalog helpers

    def product_nodes(self, products):
        """products: [(product numeric id, title, type, [(variant numeric id, size, color, price_cents, qty)])]"""
        out = []
        for pid, title, ptype, variants in products:
            nodes = []
            for vid, size, color, price, qty in variants:
                g = f"gid://shopify/ProductVariant/{vid}"
                self.variants[g] = {"price_cents": price, "qty": qty}
                self.variant_product[g] = str(pid)
                nodes.append({"id": g, "sku": f"SKU{vid}", "price": d(price), "inventoryQuantity": qty, "availableForSale": True,
                              "inventoryPolicy": "DENY", "inventoryItem": {"tracked": True},
                              "selectedOptions": [{"name": "Size", "value": size}, {"name": "Color", "value": color}]})
            out.append({"id": f"gid://shopify/Product/{pid}", "title": title, "handle": f"p{pid}", "vendor": "Acme", "productType": ptype,
                        "status": "ACTIVE", "description": "", "tags": [], "featuredImage": None, "variants": {"nodes": nodes}})
        return out

    # ------------------------------------------------------------ pricing (Shopify's own math)

    def _price(self, inp: dict) -> dict:
        sub = sum(self.variants[li["variantId"]]["price_cents"] * li["quantity"] for li in inp["lineItems"])
        disc = 0
        for code in inp.get("discountCodes") or []:
            cd = self.codes.get(code.upper())
            if cd and sub >= cd["min_cents"]:
                disc += min(cd["amount_cents"], sub)
        if inp.get("appliedDiscount"):
            a = inp["appliedDiscount"]
            disc += round(sub * a["value"] / 100) if a["valueType"] == "PERCENTAGE" else c(a["value"])
        ship = c(inp["shippingLine"]["priceWithCurrency"]["amount"]) if inp.get("shippingLine") else 0
        tax = int(((sub - disc) * TAX_RATE).quantize(Decimal("1")))
        return {"totalPriceSet": money_set(sub - disc + ship + tax), "totalTaxSet": money_set(tax), "subtotalPriceSet": money_set(sub - disc),
                "totalShippingPriceSet": money_set(ship), "totalDiscountsSet": money_set(disc), "taxesIncluded": False}

    # ------------------------------------------------------------ shopper / merchant actions outside PerkPilot

    def pay(self, draft_gid: str, company="Visa", number="•••• •••• •••• 4242", test=True) -> str:
        """The shopper completes Shopify checkout for a draft order's invoice."""
        dr = self.drafts[draft_gid]
        return self._make_order(dr, company, number, test, extra=self.charge_extra_cents)

    def _make_order(self, dr, company, number, test, extra=0):
        oid = self._id("Order")
        totals = self._price(dr["input"])
        total = c(totals["totalPriceSet"]["shopMoney"]["amount"]) + extra
        lines = [{"id": self._id("LineItem"), "title": "Item", "variantTitle": "", "quantity": li["quantity"], "currentQuantity": li["quantity"],
                  "product": {"id": "gid://shopify/Product/" + self._product_of(li["variantId"])}, "variant": {"id": li["variantId"]},
                  "originalUnitPriceSet": money_set(self.variants[li["variantId"]]["price_cents"])} for li in dr["input"]["lineItems"]]
        for li in dr["input"]["lineItems"]:
            self.variants[li["variantId"]]["qty"] -= li["quantity"]
        tx = [{"id": self._id("OrderTransaction"), "kind": "SALE", "status": "SUCCESS", "gateway": "bogus", "test": test,
               "processedAt": "2026-10-18T10:00:00Z", "amountSet": money_set(total),
               "paymentDetails": {"company": company, "number": number}}] if total else []
        self.orders[oid] = {"id": oid, "name": f"#{self.n}", "test": test, "createdAt": "2026-10-18T10:00:00Z", "processedAt": "2026-10-18T10:00:00Z",
                            "cancelledAt": None, "tags": dr["input"].get("tags", []), "displayFinancialStatus": "PAID",
                            "displayFulfillmentStatus": "UNFULFILLED", "currentTotalPriceSet": money_set(total),
                            "totalReceivedSet": money_set(total), "totalRefundedSet": money_set(0),
                            "customAttributes": dr["input"].get("customAttributes", []), "lineItems": {"nodes": lines}, "transactions": tx,
                            "fulfillments": [], "refunds": [],
                            "fulfillmentOrders": {"nodes": [{"id": self._id("FulfillmentOrder"), "status": "OPEN",
                                                             "lineItems": {"nodes": [{"id": self._id("FulfillmentOrderLineItem"),
                                                                                      "remainingQuantity": li["quantity"]} for li in lines]}}]}}
        dr["status"], dr["order"] = "COMPLETED", {"id": oid, "name": self.orders[oid]["name"]}
        return oid

    def _product_of(self, variant_gid):
        return self.variant_product[variant_gid]

    def deliver_wrong_variant(self, order_gid, variant_gid):
        """The warehouse ships a different variant than ordered."""
        o = self.orders[order_gid]
        self._fulfill(o)
        o["fulfillments"][0]["fulfillmentLineItems"]["nodes"][0]["lineItem"]["variant"]["id"] = variant_gid
        self._deliver(o)

    # ------------------------------------------------------------ fulfillment

    def _fulfill(self, o):
        line = o["lineItems"]["nodes"][0]
        o["fulfillments"].append({"id": self._id("Fulfillment"), "status": "SUCCESS", "displayStatus": "FULFILLED", "createdAt": "2026-10-19T10:00:00Z",
                                  "inTransitAt": None, "deliveredAt": None, "estimatedDeliveryAt": None,
                                  "trackingInfo": [{"company": "PerkPilot test carrier", "number": "PP1", "url": None}],
                                  "fulfillmentLineItems": {"nodes": [{"id": self._id("FulfillmentLineItem"), "quantity": line["quantity"],
                                                                      "lineItem": {"id": line["id"], "variant": {"id": line["variant"]["id"]}}}]}})
        o["displayFulfillmentStatus"] = "FULFILLED"
        for fo in o["fulfillmentOrders"]["nodes"]:
            fo["status"] = "CLOSED"
            for li in fo["lineItems"]["nodes"]:
                li["remainingQuantity"] = 0

    def _deliver(self, o):
        for f in o["fulfillments"]:
            f.update(deliveredAt="2026-10-21T21:00:00Z", displayStatus="DELIVERED")

    # ------------------------------------------------------------ GraphQL dispatch

    def handle(self, query: str, v: dict) -> dict:
        op = re.search(r"(?:query|mutation)\s+(\w+)", query)
        name = op.group(1) if op else "anonymous"
        self.calls.append((name, v))
        RECORDED.append((name, v))
        if name == "PerkPilotShop":
            return {"shop": {"name": "Acme Apparel", "currencyCode": "USD", "myshopifyDomain": "acme.myshopify.com"}}
        if name == "PerkPilotCalculate":
            return {"draftOrderCalculate": {"calculatedDraftOrder": self._price(v["input"]), "userErrors": []}}
        if name == "PerkPilotDraft":
            g = self._id("DraftOrder")
            self.drafts[g] = {"id": g, "name": f"#D{self.n}", "input": v["input"], "status": "OPEN", "order": None}
            return {"draftOrderCreate": {"draftOrder": {"id": g, "name": f"#D{self.n}", "invoiceUrl": f"https://acme.myshopify.com/1/invoices/{self.n}",
                                                        "status": "OPEN", "reserveInventoryUntil": v["input"].get("reserveInventoryUntil"),
                                                        **self._price(v["input"])}, "userErrors": []}}
        if name == "PerkPilotDraftStatus":
            dr = self.drafts.get(v["id"])
            return {"draftOrder": dr and {"id": dr["id"], "status": dr["status"], "order": dr["order"]}}
        if name == "PerkPilotComplete":
            dr = self.drafts[v["id"]]
            self._make_order(dr, None, None, test=True)
            return {"draftOrderComplete": {"draftOrder": {"id": dr["id"], "status": dr["status"], "order": dr["order"]}, "userErrors": []}}
        if name == "PerkPilotOrder":
            return {"order": json.loads(json.dumps(self.orders.get(v["id"])))}
        if name == "PerkPilotVariants":
            return {"nodes": [{"id": g, "price": d(self.variants[g]["price_cents"]), "inventoryQuantity": self.variants[g]["qty"],
                               "availableForSale": True, "inventoryPolicy": "DENY", "inventoryItem": {"tracked": True}}
                              if g in self.variants else None for g in v["ids"]]}
        if name == "PerkPilotSuggestedRefund":
            o = self.orders[v["id"]]
            left = c(o["currentTotalPriceSet"]["shopMoney"]["amount"]) - c(o["totalRefundedSet"]["shopMoney"]["amount"])
            sale = o["transactions"][0]
            return {"order": {"suggestedRefund": {"amountSet": money_set(left), "suggestedTransactions": [
                {"gateway": sale["gateway"], "kind": "SUGGESTED_REFUND", "parentTransaction": {"id": sale["id"]}, "amountSet": money_set(left)}]}}}
        if name == "PerkPilotRefund":
            inp = v["input"]
            o = self.orders[inp["orderId"]]
            amount = sum(c(t["amount"]) for t in inp.get("transactions") or []) or sum(
                c(m["storeCreditRefund"]["amount"]["amount"]) for m in inp.get("refundMethods") or [])
            rid = self._id("Refund")
            o["refunds"].append({"id": rid, "note": inp.get("note"), "createdAt": "2026-10-22T10:00:00Z", "totalRefundedSet": money_set(amount)})
            refunded = c(o["totalRefundedSet"]["shopMoney"]["amount"]) + amount
            o["totalRefundedSet"] = money_set(refunded)
            o["displayFinancialStatus"] = "REFUNDED" if refunded >= c(o["currentTotalPriceSet"]["shopMoney"]["amount"]) else "PARTIALLY_REFUNDED"
            return {"refundCreate": {"refund": {"id": rid, "totalRefundedSet": money_set(amount)}, "userErrors": []}}
        if name == "PerkPilotReturn":
            return {"returnCreate": {"return": {"id": self._id("Return"), "name": "#R1", "status": "OPEN"}, "userErrors": []}}
        if name == "PerkPilotTags":
            self.orders[v["id"]]["tags"] += v["tags"]
            return {"tagsAdd": {"userErrors": []}}
        if name == "PerkPilotFulfill":
            fo_id = v["fulfillment"]["lineItemsByFulfillmentOrder"][0]["fulfillmentOrderId"]
            o = next(o for o in self.orders.values() if any(fo["id"] == fo_id for fo in o["fulfillmentOrders"]["nodes"]))
            self._fulfill(o)
            return {"fulfillmentCreate": {"fulfillment": {"id": o["fulfillments"][-1]["id"], "status": "SUCCESS"}, "userErrors": []}}
        if name == "PerkPilotFulfillmentEvent":
            o = next(o for o in self.orders.values() if any(f["id"] == v["event"]["fulfillmentId"] for f in o["fulfillments"]))
            self._deliver(o)
            return {"fulfillmentEventCreate": {"fulfillmentEvent": {"id": self._id("FulfillmentEvent"), "status": "DELIVERED"}, "userErrors": []}}
        if name == "PerkPilotGuidance":
            for mf in v["metafields"]:
                self.metafields[mf["ownerId"]] = json.loads(mf["value"])
            return {"metafieldsSet": {"metafields": [{"id": self._id("Metafield")}], "userErrors": []}}
        if name == "PerkPilotGuidanceDelete":
            for mf in v["metafields"]:
                self.metafields.pop(mf["ownerId"], None)
            return {"metafieldsDelete": {"userErrors": []}}
        if name == "PerkPilotDiscounts":
            return {"codeDiscountNodes": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [
                {"id": cd["id"], "codeDiscount": {"__typename": "DiscountCodeBasic", "title": code, "status": "ACTIVE", "endsAt": None,
                                                  "codes": {"nodes": [{"code": code}]},
                                                  "minimumRequirement": {"__typename": "DiscountMinimumSubtotal",
                                                                         "greaterThanOrEqualToSubtotal": {"amount": d(cd["min_cents"])}},
                                                  "customerGets": {"value": {"__typename": "DiscountAmount", "amount": {"amount": d(cd["amount_cents"])},
                                                                             "appliesOnEachItem": False},
                                                                   "items": {"__typename": "AllDiscountItems"}}}}
                for code, cd in self.codes.items()]}}
        if name == "PerkPilotDiscount":
            dc = v["discount"]
            self.codes[dc["code"].upper()] = {"id": self._id("DiscountCodeNode"),
                                              "amount_cents": c(dc["customerGets"]["value"]["discountAmount"]["amount"]),
                                              "min_cents": c(dc["minimumRequirement"]["subtotal"]["greaterThanOrEqualToSubtotal"])}
            return {"discountCodeBasicCreate": {"codeDiscountNode": {"id": self.codes[dc["code"].upper()]["id"]}, "userErrors": []}}
        raise AssertionError(f"FakeShopify: unhandled operation {name}")

    def transport(self) -> httpx.MockTransport:
        def route(req: httpx.Request):
            if req.url.path.endswith("/oauth/access_token"):
                return httpx.Response(200, json={"access_token": "tok", "expires_in": 86399})
            body = json.loads(req.content)
            return httpx.Response(200, json={"data": self.handle(body["query"], body.get("variables") or {})})
        return httpx.MockTransport(route)
