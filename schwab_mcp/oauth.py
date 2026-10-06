"""OAuth 2 authorization-code flow and token refresh for the Schwab API.

Only two HTTP calls live here: exchanging an authorization code and refreshing
an access token, both POSTs to Schwab's OAuth token endpoint. No API data
endpoint is ever called with anything but GET (see client.py).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .config import Settings
from .errors import SchwabAPIError, SessionExpired
from .tokens import TokenStore

AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"

# Schwab refresh tokens are valid for 7 days from the initial login and are not
# extended by refreshing. Access tokens last 30 minutes (expires_in=1800).
REFRESH_TOKEN_LIFETIME = 7 * 24 * 3600
ACCESS_TOKEN_SKEW = 60  # refresh this many seconds before expiry
TOKEN_FIELDS = ("access_token", "refresh_token", "token_type", "scope")


def build_authorize_url(settings: Settings, state: str) -> str:
    query = urlencode(
        {
            "client_id": settings.app_key,
            "redirect_uri": settings.callback_url,
            "response_type": "code",
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def code_from_redirect(redirected_url: str, expected_state: str) -> str:
    """Pull the authorization code out of the URL the browser was sent to."""
    params = parse_qs(urlparse(redirected_url.strip()).query)
    if "error" in params:
        raise SchwabAPIError(f"Schwab returned an error during login: {params['error'][0]}")
    codes = params.get("code")
    if not codes:
        raise SchwabAPIError("That URL has no ?code= parameter. Paste the full address from the browser bar.")
    state = params.get("state", [None])[0]
    if state is not None and state != expected_state:
        raise SchwabAPIError("State mismatch: that redirect is from a different login attempt. Run auth.py again.")
    return codes[0]


def _token_request(settings: Settings, data: dict[str, str]) -> dict[str, Any]:
    return {
        "url": TOKEN_URL,
        "data": data,
        "auth": (settings.app_key, settings.app_secret),
        "headers": {"Accept": "application/json"},
    }


def normalize_token(
    payload: dict[str, Any], now: float, previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Keep only the fields we need (drops id_token) and add timestamps."""
    token = {k: payload[k] for k in TOKEN_FIELDS if k in payload}
    if "access_token" not in token:
        raise SchwabAPIError("Schwab's token response had no access_token.")
    token["expires_at"] = now + int(payload.get("expires_in", 1800))
    if previous and token.get("refresh_token", previous.get("refresh_token")) == previous.get("refresh_token"):
        # Same refresh token: its 7-day clock keeps running from the original login.
        token["refresh_token"] = previous["refresh_token"]
        token["refresh_token_issued_at"] = previous.get("refresh_token_issued_at", now)
    else:
        token["refresh_token_issued_at"] = now
    if "refresh_token" not in token:
        raise SchwabAPIError("Schwab's token response had no refresh_token.")
    return token


def exchange_code(settings: Settings, code: str, http: httpx.Client) -> dict[str, Any]:
    response = http.post(
        **_token_request(
            settings,
            {"grant_type": "authorization_code", "code": code, "redirect_uri": settings.callback_url},
        )
    )
    if response.status_code != 200:
        raise SchwabAPIError(
            f"Token exchange failed (HTTP {response.status_code}). The code is single-use and expires "
            "in about 30 seconds; run auth.py again and paste the URL promptly.",
            response.status_code,
        )
    return normalize_token(response.json(), time.time())


def refresh_token_expires_at(token: dict[str, Any]) -> float:
    return float(token.get("refresh_token_issued_at", 0)) + REFRESH_TOKEN_LIFETIME


class TokenManager:
    """Hands out a valid access token, refreshing it when needed."""

    def __init__(self, settings: Settings, store: TokenStore, clock=time.time):
        self.settings = settings
        self.store = store
        self.clock = clock
        self._token: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    def _current(self) -> dict[str, Any]:
        if self._token is None or self.clock() >= refresh_token_expires_at(self._token):
            # (Re)load from the store so a fresh auth.py login is picked up without a restart.
            self._token = self.store.load()
        if not self._token or "refresh_token" not in self._token:
            raise SessionExpired("no saved token found")
        if self.clock() >= refresh_token_expires_at(self._token):
            raise SessionExpired("refresh token is older than 7 days")
        return self._token

    async def access_token(self, http: httpx.AsyncClient, force_refresh: bool = False) -> str:
        async with self._lock:
            token = self._current()
            if force_refresh or self.clock() >= float(token.get("expires_at", 0)) - ACCESS_TOKEN_SKEW:
                token = await self._refresh(http, token)
            return token["access_token"]

    async def _refresh(self, http: httpx.AsyncClient, token: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await http.post(
                **_token_request(
                    self.settings,
                    {"grant_type": "refresh_token", "refresh_token": token["refresh_token"]},
                )
            )
        except httpx.TransportError as exc:
            raise SchwabAPIError(f"Could not reach Schwab to refresh the session ({type(exc).__name__}).") from None
        if response.status_code in (400, 401):
            # invalid_grant / unauthorized_client: the refresh token is dead.
            self._token = None
            raise SessionExpired("refresh token was rejected")
        if response.status_code != 200:
            raise SchwabAPIError(
                f"Schwab token refresh failed (HTTP {response.status_code}). Try again shortly.",
                response.status_code,
            )
        new_token = normalize_token(response.json(), self.clock(), previous=token)
        self.store.save(new_token)
        self._token = new_token
        return new_token
