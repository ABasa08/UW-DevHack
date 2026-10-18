"""Live-mode isolation and the read-only Shopify catalog adapter (no network: mocked transport)."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.pricing import sizes_in_order
from app.shopify import fetch_products, get_token, sync_catalog
from app.store import MemoryStore, create_store

from .conftest import ok


def variant(vid, size, color="Black", price="110.00", qty=5):
    return {"id": f"gid://shopify/ProductVariant/{vid}", "sku": f"SKU{vid}", "price": price, "inventoryQuantity": qty,
            "selectedOptions": [{"name": "Size", "value": size}, {"name": "Color", "value": color}]}


def node(pid, variants, title="Trail Runner", ptype="Running Shoes", status="ACTIVE"):
    return {"id": f"gid://shopify/Product/{pid}", "title": title, "handle": f"p{pid}", "vendor": "Acme", "productType": ptype,
            "status": status, "description": "", "tags": [], "featuredImage": {"url": "https://cdn.example/p.jpg"},
            "variants": {"nodes": variants}}


CATALOG = [
    node(1, [variant(11, "10"), variant(12, "10.5", qty=None)]),
    node(2, [variant(21, "M")], title="Socks"),                                         # non-numeric size
    node(3, [variant(31, "10", price="90.00"), variant(32, "11", price="95.00")]),      # mixed prices
]

# A store that sells no shoes at all: letter sizes, per-variant prices, and a product with no options.
APPAREL = [
    node(5, [variant(51, "S", "Grey", "40.00"), variant(52, "M", "Grey", "40.00"), variant(53, "XL", "Grey", "45.00")],
         title="Heavyweight Hoodie", ptype="Hoodie"),
    node(6, [{"id": "gid://shopify/ProductVariant/61", "sku": "", "price": "15.00", "inventoryQuantity": 3,
              "selectedOptions": [{"name": "Title", "value": "Default Title"}]}], title="Tote Bag", ptype="Bag"),
    node(7, [], title="Empty"),
]


@pytest.fixture()
def live(monkeypatch):
    monkeypatch.setenv("PERKPILOT_MODE", "live")
    monkeypatch.setenv("PERKPILOT_ADMIN_TOKEN", "s3cret")


def test_live_mode_has_no_seed_or_simulated_effects(live):
    store = MemoryStore()
    c = TestClient(create_app(store))
    assert ok(c.get("/api/health"))["mode"] == "live"
    assert store.find("products") == [] and store.find("shoppers") == []
    for method, path in [("post", "/api/demo/reset"), ("get", "/api/demo/state"), ("post", "/api/checkout/confirm"),
                         ("post", "/api/remedies/rem_0001/accept")]:
        assert c.request(method, path, json={}).status_code in (404, 405), path


def test_live_merchant_routes_need_token(live):
    c = TestClient(create_app(MemoryStore()))
    assert c.get("/api/merchant/claims").status_code == 401
    assert c.get("/api/merchant/claims", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/api/merchant/claims", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_live_mode_fails_closed_without_mongo(live, monkeypatch):
    monkeypatch.delenv("MONGODB_URI", raising=False)
    with pytest.raises(RuntimeError):
        create_store(live=True)
    monkeypatch.delenv("PERKPILOT_ADMIN_TOKEN")
    with pytest.raises(RuntimeError):
        create_app(MemoryStore())


def test_fetch_paginates_and_rejects_non_usd():
    pages = iter([
        {"data": {"shop": {"currencyCode": "USD"}, "products": {"pageInfo": {"hasNextPage": True, "endCursor": "c1"}, "nodes": CATALOG[:1]}}},
        {"data": {"shop": {"currencyCode": "USD"}, "products": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": CATALOG[1:]}}},
    ])
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=next(pages))

    nodes = fetch_products(httpx.Client(transport=httpx.MockTransport(handler)), "x.myshopify.com", "tok")
    assert len(nodes) == 3 and seen[0].headers["X-Shopify-Access-Token"] == "tok"
    assert str(seen[0].url) == "https://x.myshopify.com/admin/api/2026-10/graphql.json"

    cad = {"data": {"shop": {"currencyCode": "CAD"}, "products": {"pageInfo": {"hasNextPage": False}, "nodes": []}}}
    with pytest.raises(RuntimeError, match="CAD"):
        fetch_products(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=cad))), "x", "t")


def test_sync_maps_skips_and_archives():
    store = MemoryStore()
    res = sync_catalog(store, CATALOG + APPAREL)
    assert res["synced"] == ["sfy_1", "sfy_2", "sfy_3", "sfy_5", "sfy_6"]
    assert [(s["title"], s["reason"]) for s in res["skipped"]] == [("Empty", "no variants")]
    assert store.get("products", "sfy_2")["variants"][0]["size"] == "M"
    mixed = store.get("products", "sfy_3")
    assert mixed["price_cents"] == 9000 and [v["price_cents"] for v in mixed["variants"]] == [9000, 9500]
    hoodie, tote = store.get("products", "sfy_5"), store.get("products", "sfy_6")
    assert hoodie["category"] == "hoodie" and sizes_in_order(hoodie) == ["S", "M", "XL"]
    assert tote["variants"][0]["size"] == "" and tote["variants"][0]["color"] == "" and tote["variants"][0]["options"] == {}
    assert tote["inventory_by_variant"] == {"sfy_6-61": 3} and not tote["variants"][0]["stock_untracked"]
    # Untracked inventory (Shopify reports -1) or "continue selling" is available, not out of stock.
    untracked = node(8, [{**variant(81, "M", qty=-1), "availableForSale": True, "inventoryItem": {"tracked": False}},
                         {**variant(82, "L", qty=0), "availableForSale": True, "inventoryPolicy": "CONTINUE"},
                         {**variant(83, "XL", qty=0), "availableForSale": False, "inventoryPolicy": "DENY"}])
    sync_catalog(other := MemoryStore(), [untracked])
    assert other.get("products", "sfy_8")["inventory_by_variant"] == {"sfy_8-81": 99, "sfy_8-82": 99, "sfy_8-83": 0}
    p = store.get("products", "sfy_1")
    assert p["price_cents"] == 11000 and p["category"] == "running_shoes" and p["brand"] == "Acme"
    assert p["inventory_by_variant"] == {"sfy_1-11": 5, "sfy_1-12": 0}  # unknown inventory -> 0, never assumed
    assert [v["color"] for v in p["variants"]] == ["black", "black"]
    # removed from Shopify -> archived on next sync
    assert "sfy_1" in sync_catalog(store, [])["archived"] and store.get("products", "sfy_1")["status"] == "archived"


def verify_sfy(client, iid, vid="sfy_1-11"):
    return ok(client.post("/api/checkout/verify", json={"intent_id": iid, "proposal": {
        "product_id": "sfy_1", "variant_id": vid, "quantity": 1, "shipping_option": "standard", "coupon_codes": [],
        "card_offer_ids": [], "payment_method_id": "pm_visa_4242", "source": "shopper"}}))["result"]


def test_synced_product_verification_fails_closed(client, confirmed_intent):
    """Demo app + a Shopify product: unknown shipping/tax must FAIL, but every knowable check still runs."""
    sync_catalog(client.app.state.store, CATALOG[:1], shop="x.myshopify.com")
    iid = confirmed_intent()["intent_id"]  # black, size 10, max $120
    r = verify_sfy(client, iid)
    assert r["status"] == "FAIL" and [c["rule"] for c in r["checks"] if c["status"] == "FAIL"] == ["TOTAL_UNKNOWN"]
    assert {"COLOR", "SIZE", "STOCK"} <= {c["rule"] for c in r["checks"] if c["status"] == "PASS"}
    assert "SIZE" in [c["rule"] for c in verify_sfy(client, iid, "sfy_1-12")["checks"] if c["status"] == "FAIL"]
    # item price alone over budget is provably a violation
    cheap = confirmed_intent(max_charge_cents=100_00)["intent_id"]
    assert "BUDGET" in [c["rule"] for c in verify_sfy(client, cheap)["checks"] if c["status"] == "FAIL"]
    assert ok(client.get("/api/products/sfy_1"))["product"]["source"] == "shopify"


def test_recommendations_hand_off_to_shopify_checkout(client, confirmed_intent):
    store = client.app.state.store
    sync_catalog(store, CATALOG[:1], shop="x.myshopify.com", only_shopify=True)
    assert [p["_id"] for p in store.find("products", {"status": "active"})] == ["sfy_1"]  # demo catalog archived
    recs = ok(client.post("/api/recommendations", json={"intent_id": confirmed_intent()["intent_id"]}))
    [c] = recs["candidates"]
    assert c["checkout_url"] == "https://x.myshopify.com/cart/11:1" and not c["eligible"] and recs["top_pick"] is None
    # over budget -> ruled out, no checkout link
    recs = ok(client.post("/api/recommendations", json={"intent_id": confirmed_intent(max_charge_cents=100_00)["intent_id"]}))
    assert recs["candidates"][0]["checkout_url"] is None and "exceeds" in recs["candidates"][0]["reasons"][0]
    p = store.get("products", "sfy_1")
    assert p["image"]["url"] == "https://cdn.example/p.jpg" and p["storefront_url"] == "https://x.myshopify.com/products/p1"


def test_get_token_prefers_static_then_client_credentials(monkeypatch):
    monkeypatch.setenv("SHOPIFY_ADMIN_TOKEN", "shpat_x")
    assert get_token(httpx.Client(transport=httpx.MockTransport(lambda r: 1 / 0)), "x.myshopify.com") == "shpat_x"
    monkeypatch.delenv("SHOPIFY_ADMIN_TOKEN")
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "sec")
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"access_token": "tok24h", "expires_in": 86399})

    assert get_token(httpx.Client(transport=httpx.MockTransport(handler)), "x.myshopify.com") == "tok24h"
    assert str(seen[0].url) == "https://x.myshopify.com/admin/oauth/access_token"
    assert b"grant_type=client_credentials" in seen[0].content and b"client_secret=sec" in seen[0].content


def test_any_catalog_parses_confirms_and_matches(client):
    """A hoodie/bag store: categories and sizes come from the catalog, sizes aren't required where none exist."""
    store = client.app.state.store
    sync_catalog(store, APPAREL, shop="x.myshopify.com", only_shopify=True)
    assert ok(client.get("/api/catalog/facets")) == {"categories": ["bag", "hoodie"], "sized_categories": ["hoodie"],
                                                     "colors": ["grey"], "sizes": ["S", "M", "XL"]}
    it = ok(client.post("/api/intent/parse", json={"shopper_id": "shp_jordan", "text": "a grey hoodie in medium under $50"}))
    req = it["parsed_requirements"]
    assert (req["category"], req["color"], req["size"], req["max_charge_cents"]) == ("hoodie", "grey", "M", 5000)
    ok(client.post("/api/intent/confirm", json={"intent_id": it["intent_id"], "confirmed_requirements": req}))
    [c] = ok(client.post("/api/recommendations", json={"intent_id": it["intent_id"]}))["candidates"]
    assert c["variant_id"] == "sfy_5-52" and c["checkout_url"] == "https://x.myshopify.com/cart/52:1"
    # Per-variant price: the $45 XL is over a $42 limit even though the hoodie's "from" price is $40.
    xl = {**req, "size": "XL", "max_charge_cents": 4200}
    ok(client.post("/api/intent/confirm", json={"intent_id": it["intent_id"], "confirmed_requirements": xl}))
    [c] = ok(client.post("/api/recommendations", json={"intent_id": it["intent_id"]}))["candidates"]
    assert c["checkout_url"] is None and "exceeds" in c["reasons"][0]
    # A bag has no sizes, so none is required; an unknown category is refused.
    bag = ok(client.post("/api/intent/parse", json={"shopper_id": "shp_jordan", "text": "a bag under $20"}))
    assert bag["parsed_requirements"]["category"] == "bag" and "size" not in bag["missing_essential"]
    ok(client.post("/api/intent/confirm", json={"intent_id": bag["intent_id"], "confirmed_requirements": bag["parsed_requirements"]}))
    r = client.post("/api/intent/confirm", json={"intent_id": bag["intent_id"],
                                                 "confirmed_requirements": {**bag["parsed_requirements"], "category": "running_shoes"}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_category"
