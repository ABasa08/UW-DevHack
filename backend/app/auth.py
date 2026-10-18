"""Who is calling: shoppers in the storefront embed, and merchant staff in Shopify admin.

* Shopper sessions are short HMAC-signed tokens PerkPilot issues. A signed-in Shopify customer gets one through the
  app proxy (``/apps/perkpilot/session``), whose signature proves Shopify forwarded the request and vouches for
  ``logged_in_customer_id``. Guests get an anonymous session tied to their browser.
* Merchant requests from the embedded admin carry an App Bridge session token (a JWT signed with the app's client
  secret). Outside Shopify admin, the PERKPILOT_ADMIN_TOKEN bearer still works for local use.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlparse

from .core import DomainError
from .store import BaseStore

SESSION_TTL_S = 30 * 24 * 3600


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _session_key() -> bytes:
    secret = os.environ.get("PERKPILOT_SESSION_SECRET") or os.environ.get("SHOPIFY_CLIENT_SECRET") or os.environ.get("PERKPILOT_ADMIN_TOKEN")
    if not secret:
        raise DomainError("Shopper sessions need PERKPILOT_SESSION_SECRET (or the Shopify client secret) on the server.", 503,
                          "sessions_not_configured")
    return hashlib.sha256(b"perkpilot-session:" + secret.encode()).digest()


# ---------------------------------------------------------------- shopper sessions


def issue_session(store: BaseStore, *, customer_id: str | None = None, previous: str | None = None) -> dict:
    """A signed-in customer always maps to the same shopper; a guest keeps their previous anonymous id when it's valid."""
    if customer_id:
        sid = f"cust_{customer_id}"
    else:
        prior = read_session(previous) if previous else None
        sid = prior["sid"] if prior and prior["sid"].startswith("anon_") else f"anon_{secrets.token_hex(8)}"
    ensure_shopper(store, sid, customer_id)
    payload = {"sid": sid, "cid": customer_id, "exp": int(time.time()) + SESSION_TTL_S}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_session_key(), body.encode(), hashlib.sha256).digest())
    return {"token": f"{body}.{sig}", "shopper_id": sid, "signed_in": bool(customer_id), "expires_at": payload["exp"]}


def read_session(token: str | None) -> dict | None:
    if not token or token.count(".") != 1:
        return None
    body, sig = token.split(".")
    try:
        good = hmac.compare_digest(_b64(hmac.new(_session_key(), body.encode(), hashlib.sha256).digest()), sig)
        payload = json.loads(_unb64(body)) if good else None
    except (ValueError, DomainError):
        return None
    if not payload or payload.get("exp", 0) < time.time():
        return None
    return payload


def ensure_shopper(store: BaseStore, sid: str, customer_id: str | None = None) -> dict:
    s = store.get("shoppers", sid)
    if not s:
        from .shopify_commerce import PM_OTHER, PM_VISA

        s = {"_id": sid, "shopper_id": sid, "display_name": "Customer" if customer_id else "Guest",
             "role": "Signed-in Shopify customer" if customer_id else "Guest shopper", "payment_methods": [PM_VISA, PM_OTHER],
             "default_payment_method": PM_VISA, "shopify_customer_id": customer_id}
        store.put("shoppers", s)
    return s


def verify_app_proxy(query: list[tuple[str, str]], secret: str) -> bool:
    """Shopify app proxy signature: hex HMAC-SHA256 over the sorted 'key=value' pairs (multi-values comma-joined),
    concatenated without separators, excluding 'signature'. Requests older than 5 minutes are refused."""
    params: dict[str, list[str]] = {}
    given = ""
    for k, v in query:
        if k == "signature":
            given = v
        else:
            params.setdefault(k, []).append(v)
    if not secret or not given:
        return False
    message = "".join(f"{k}={','.join(v)}" for k, v in sorted(params.items()))
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    ts = params.get("timestamp", ["0"])[0]
    fresh = ts.isdigit() and abs(time.time() - int(ts)) < 300
    return hmac.compare_digest(digest, given) and fresh


# ---------------------------------------------------------------- merchant (embedded admin)


def verify_admin_session(token: str, *, client_id: str, secret: str, shop: str) -> dict | None:
    """App Bridge session token: HS256 JWT, audience = our client id, destination = this shop, not expired."""
    try:
        head, body, sig = token.split(".")
        header, claims = json.loads(_unb64(head)), json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if header.get("alg") != "HS256" or not secret or not client_id:
        return None
    expected = _b64(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        return None
    t = time.time()
    if claims.get("aud") != client_id or claims.get("exp", 0) < t - 5 or claims.get("nbf", 0) > t + 5:
        return None
    if shop and urlparse(claims.get("dest", "")).hostname != shop:
        return None
    return claims


def merchant_actor(authorization: str) -> str | None:
    """'shopify_staff:<user id>' for a valid admin session, 'admin_token' for the bearer token, else None."""
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        return None
    admin = os.environ.get("PERKPILOT_ADMIN_TOKEN", "")
    if admin and secrets.compare_digest(token.encode(), admin.encode()):
        return "admin_token"
    claims = verify_admin_session(token, client_id=os.environ.get("SHOPIFY_CLIENT_ID", ""), secret=os.environ.get("SHOPIFY_CLIENT_SECRET", ""),
                                  shop=os.environ.get("SHOPIFY_SHOP", "").removeprefix("https://").strip("/"))
    return f"shopify_staff:{claims.get('sub', 'unknown')}" if claims else None
