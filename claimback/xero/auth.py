"""Xero OAuth2 (authorization code flow) with local token cache + refresh.

One-time setup:
  1. developer.xero.com -> My Apps -> New app (Web app)
  2. Redirect URI: http://localhost:8912/callback
  3. Put client id/secret in .env
  4. `claimback auth` — opens browser, captures the callback, caches tokens.

Tokens auto-refresh on expiry (access tokens last 30 min; refresh token
rotates on every refresh — the cache always stores the newest pair).
"""
from __future__ import annotations

import base64
import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from ..config import settings

AUTH_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"


class TokenCache:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or settings.token_cache_path)

    def load(self) -> dict | None:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return None

    def save(self, tokens: dict) -> None:
        tokens["obtained_at"] = time.time()
        self.path.write_text(json.dumps(tokens, indent=2))


class _CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None

    def do_GET(self):  # noqa: N802
        qs = parse_qs(urlparse(self.path).query)
        _CallbackHandler.code = (qs.get("code") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>ClaimBack connected to Xero. You can close this tab.</h2>")

    def log_message(self, *args):  # silence
        pass


def _basic_auth() -> str:
    raw = f"{settings.xero_client_id}:{settings.xero_client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def authorize() -> dict:
    """Interactive browser flow. Returns and caches the token set."""
    params = {
        "response_type": "code",
        "client_id": settings.xero_client_id,
        "redirect_uri": settings.xero_redirect_uri,
        "scope": settings.xero_scopes,
        "state": "claimback",
    }
    url = f"{AUTH_URL}?{urlencode(params)}"
    port = urlparse(settings.xero_redirect_uri).port or 8912
    server = HTTPServer(("localhost", port), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    print(f"Opening browser for Xero consent…\n{url}")
    webbrowser.open(url)
    thread.join(timeout=300)
    server.server_close()
    if not _CallbackHandler.code:
        raise RuntimeError("No authorization code received (timed out after 5 min)")

    resp = httpx.post(TOKEN_URL, headers={"Authorization": _basic_auth()}, data={
        "grant_type": "authorization_code",
        "code": _CallbackHandler.code,
        "redirect_uri": settings.xero_redirect_uri,
    })
    resp.raise_for_status()
    tokens = resp.json()
    tokens["tenant_id"] = _fetch_tenant(tokens["access_token"])
    TokenCache().save(tokens)
    return tokens


def _fetch_tenant(access_token: str) -> str:
    resp = httpx.get(CONNECTIONS_URL, headers={"Authorization": f"Bearer {access_token}"})
    resp.raise_for_status()
    connections = resp.json()
    if not connections:
        raise RuntimeError("No Xero organisations connected to this token")
    return connections[0]["tenantId"]


def get_access() -> tuple[str, str]:
    """Return (access_token, tenant_id), refreshing if expired."""
    cache = TokenCache()
    tokens = cache.load()
    if tokens is None:
        raise RuntimeError("Not authorised — run `claimback auth` first")
    age = time.time() - tokens.get("obtained_at", 0)
    if age > tokens.get("expires_in", 1800) - 60:
        resp = httpx.post(TOKEN_URL, headers={"Authorization": _basic_auth()}, data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
        })
        resp.raise_for_status()
        new = resp.json()
        new["tenant_id"] = tokens["tenant_id"]
        cache.save(new)
        tokens = new
    return tokens["access_token"], tokens["tenant_id"]
