"""Read-only Shopify catalog adapter.

Pulls products from the Shopify Admin GraphQL API and upserts them into the same
``products`` documents the fixtures use, so the assistant, recommendations and verifier
work unchanged. Shopify stays authoritative: this is a projection, re-run it (or wire it
to product webhooks) to converge. Products missing from Shopify are archived, not deleted.

Pilot constraints (skipped products are reported, never silently coerced):
  * all variants of a product share one price (the domain prices per product);
  * sizes are numeric US-style values (e.g. "10", "10.5");
  * shipping and tax are unknown until Shopify checkout, so the verifier fails closed.

Usage:  python -m app.shopify sync [--only-shopify]   (--only-shopify archives the fictional demo catalog)
Env:    SHOPIFY_SHOP (my-store.myshopify.com) plus either SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET
        (Dev Dashboard app, client credentials grant) or SHOPIFY_ADMIN_TOKEN (an existing shpat_ token).
        Optional SHOPIFY_API_VERSION, SHOPIFY_SIZE_OPTION (default "Size"), SHOPIFY_COLOR_OPTION (default "Color").
"""

from __future__ import annotations

import os
import re
from decimal import Decimal

import httpx

from .core import MERCHANT_ID, live_mode, load_env
from .store import BaseStore

API_VERSION = "2026-10"
PREFIX = "sfy_"
NUMERIC_SIZE = re.compile(r"^\d{1,2}(\.5)?$")
DEFAULT_HEX = "#8a8f98"

QUERY = """
query Products($after: String) {
  shop { currencyCode }
  products(first: 50, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id title handle vendor productType status description tags featuredImage { url }
      variants(first: 100) {
        nodes { id sku price inventoryQuantity selectedOptions { name value } }
      }
    }
  }
}
"""


def get_token(client: httpx.Client, shop: str) -> str:
    """Static token if given, else exchange Dev Dashboard app credentials (token lives 24h; sync is one-shot)."""
    if os.environ.get("SHOPIFY_ADMIN_TOKEN"):
        return os.environ["SHOPIFY_ADMIN_TOKEN"]
    r = client.post(f"https://{shop}/admin/oauth/access_token", timeout=30, data={
        "grant_type": "client_credentials",
        "client_id": os.environ["SHOPIFY_CLIENT_ID"], "client_secret": os.environ["SHOPIFY_CLIENT_SECRET"]})
    r.raise_for_status()
    return r.json()["access_token"]


def fetch_products(client: httpx.Client, shop: str, token: str, version: str = API_VERSION) -> list[dict]:
    url = f"https://{shop}/admin/api/{version}/graphql.json"
    out, after = [], None
    while True:
        r = client.post(url, json={"query": QUERY, "variables": {"after": after}},
                        headers={"X-Shopify-Access-Token": token}, timeout=30)
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise RuntimeError(f"Shopify GraphQL error: {body['errors']}")
        if body["data"]["shop"]["currencyCode"] != "USD":
            # ponytail: USD-only pilot; per-currency minor units + presentment prices before multi-currency.
            raise RuntimeError(f"Shop currency {body['data']['shop']['currencyCode']} is not supported yet (USD only).")
        page = body["data"]["products"]
        out += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return out
        after = page["pageInfo"]["endCursor"]


def _numeric_id(gid: str) -> str:
    return gid.rsplit("/", 1)[-1]


def map_product(node: dict, size_option: str = "Size", color_option: str = "Color", shop: str = "") -> tuple[dict | None, str | None]:
    """Shopify product node -> PerkPilot product doc, or (None, reason) when outside the pilot constraints."""
    pid = PREFIX + _numeric_id(node["id"])
    variants, inventory, prices = [], {}, set()
    for v in node["variants"]["nodes"]:
        opts = {o["name"].lower(): o["value"] for o in v["selectedOptions"]}
        size = opts.get(size_option.lower(), "").strip()
        if not NUMERIC_SIZE.match(size):
            continue
        vid = f"{pid}-{_numeric_id(v['id'])}"
        variants.append({"variant_id": vid, "shopify_gid": v["id"], "color": opts.get(color_option.lower(), "").strip().lower(),
                         "color_hex": DEFAULT_HEX, "size": size, "sku": v.get("sku") or "",
                         # Shopify cart permalink: adds this variant and opens the store's own checkout.
                         "checkout_url": f"https://{shop}/cart/{_numeric_id(v['id'])}:1" if shop else None})
        inventory[vid] = max(v.get("inventoryQuantity") or 0, 0)
        prices.add(int(Decimal(v["price"]) * 100))  # Money is a decimal string; USD minor units are exact cents
    if not variants:
        return None, f"no variants with a numeric '{size_option}' option"
    if len(prices) > 1:
        return None, "variants have different prices"
    return {
        "_id": pid, "product_id": pid, "shopify_gid": node["id"], "source": "shopify",
        # ponytail: single merchant; add a shop/tenant id on every record before serving a second store.
        "merchant_id": MERCHANT_ID,
        "name": node["title"], "brand": node.get("vendor") or "",
        "category": (node.get("productType") or "uncategorized").strip().lower().replace(" ", "_"),
        "description": node.get("description") or "",
        "variants": variants, "available_variants": [v["variant_id"] for v in variants], "inventory_by_variant": inventory,
        "price_cents": prices.pop(), "demo_tax_cents": 0, "currency": "USD", "delivery_options": [],
        "size_information": {"system": "as listed", "fit_note": "", "chart": []},
        "status": "active" if node.get("status") == "ACTIVE" else "archived",
        "image": {"accent_hex": DEFAULT_HEX, "url": (node.get("featuredImage") or {}).get("url")},
        "storefront_url": f"https://{shop}/products/{node['handle']}" if shop and node.get("handle") else None, "tags": node.get("tags") or [], "rating": None,
    }, None


def sync_catalog(store: BaseStore, nodes: list[dict], size_option: str = "Size", color_option: str = "Color",
                 shop: str = "", only_shopify: bool = False) -> dict:
    seen, skipped = set(), []
    for node in nodes:
        doc, reason = map_product(node, size_option, color_option, shop)
        if doc is None:
            skipped.append({"shopify_gid": node["id"], "title": node.get("title"), "reason": reason})
            continue
        store.put("products", doc)
        seen.add(doc["_id"])
    archived = []
    for p in store.find("products"):
        stale = p.get("source") == "shopify" and p["_id"] not in seen
        if (stale or (only_shopify and p.get("source") != "shopify")) and p["status"] != "archived":
            store.put("products", {**p, "status": "archived"})
            archived.append(p["_id"])
    return {"synced": sorted(seen), "skipped": skipped, "archived": archived}


def main(only_shopify: bool = False) -> None:
    from .store import create_store

    load_env()
    shop = os.environ["SHOPIFY_SHOP"].removeprefix("https://").strip("/")
    with httpx.Client() as client:
        nodes = fetch_products(client, shop, get_token(client, shop), os.environ.get("SHOPIFY_API_VERSION", API_VERSION))
    store = create_store(live=live_mode())
    res = sync_catalog(store, nodes, os.environ.get("SHOPIFY_SIZE_OPTION", "Size"), os.environ.get("SHOPIFY_COLOR_OPTION", "Color"),
                       shop, only_shopify)
    print(f"synced {len(res['synced'])}, skipped {len(res['skipped'])}, archived {len(res['archived'])}")
    for s in res["skipped"]:
        print(f"  skipped {s['title']!r}: {s['reason']}")


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if args not in (["sync"], ["sync", "--only-shopify"]):
        sys.exit("usage: python -m app.shopify sync [--only-shopify]")
    main(only_shopify="--only-shopify" in args)
