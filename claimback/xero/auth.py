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
    error: str | None = None

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        code = (qs.get("code") or [None])[0]
        error = (qs.get("error") or [None])[0]
        if parsed.path != "/callback" or (code is None and error is None):
            # favicon requests, browser prefetches etc. — ignore, keep waiting
            self.send_response(204)
            self.end_headers()
            return
        if (qs.get("state") or [None])[0] != "claimback":
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<h2>State mismatch - possible CSRF. Re-run claimback auth.</h2>")
            return
        _CallbackHandler.code, _CallbackHandler.error = code, error
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if error:
            self.wfile.write(f"<h2>Xero returned an error: {error}. Check the terminal.</h2>".encode())
        else:
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
    _CallbackHandler.code = _CallbackHandler.error = None
    server = HTTPServer(("localhost", port), _CallbackHandler)
    server.timeout = 1  # so the loop below can check the deadline between requests
    print(f"Opening browser for Xero consent…\n{url}")
    webbrowser.open(url)
    # Serve until a real callback arrives — stray requests (favicon, prefetch)
    # must not consume the flow, which is why handle_request() runs in a loop.
    deadline = time.time() + 300
    while _CallbackHandler.code is None and _CallbackHandler.error is None and time.time() < deadline:
        server.handle_request()
    server.server_close()
    if _CallbackHandler.error:
        raise RuntimeError(f"Xero consent failed: {_CallbackHandler.error}")
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
