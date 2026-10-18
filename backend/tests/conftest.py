import os
import tempfile

import pytest

# Keep the module-level app from touching the real demo state file, and force the
# deterministic interpreter so tests never depend on a live model.
os.environ.setdefault("PERKPILOT_DATA_DIR", tempfile.mkdtemp(prefix="perkpilot-test-"))
os.environ["PERKPILOT_AI_MODE"] = "fallback"
os.environ.pop("MONGODB_URI", None)

from fastapi.testclient import TestClient  # noqa: E402

import app.core  # noqa: E402

app.core.load_env = lambda: None  # tests never read the developer's backend/.env (keys, admin token, shop)

from app.main import create_app  # noqa: E402
from app.store import MemoryStore  # noqa: E402

REQUEST = "Find black running shoes in size 10, under $120 including shipping and tax, delivered by Friday."


@pytest.fixture()
def store():
    return MemoryStore()


@pytest.fixture()
def client(store):
    return TestClient(create_app(store))


@pytest.fixture()
def admin(monkeypatch):
    """Bearer header for the token-protected /api/admin audit routes."""
    monkeypatch.setenv("PERKPILOT_ADMIN_TOKEN", "t0ken")
    return {"Authorization": "Bearer t0ken"}


def ok(resp):
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture()
def confirmed_intent(client):
    def make(shopper="shp_jordan", text=REQUEST, **overrides):
        it = ok(client.post("/api/intent/parse", json={"shopper_id": shopper, "text": text}))
        req = {**it["parsed_requirements"], **overrides}
        return ok(client.post("/api/intent/confirm", json={"intent_id": it["intent_id"], "confirmed_requirements": req}))
    return make


def proposal(**kw):
    p = {"product_id": "aerox", "variant_id": "aerox-bla-10", "quantity": 1, "shipping_option": "standard",
         "coupon_codes": ["STRIDE15"], "card_offer_ids": [], "payment_method_id": "pm_visa_4242", "source": "shopper"}
    p.update(kw)
    return p


def failed_rules(quote):
    return [c["rule"] for c in quote["result"]["checks"] if c["status"] == "FAIL"]
