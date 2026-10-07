"""HTTP client for the Schwab Trader API.

Reads go through ``get`` (read-only paths only). The only writes are the three
order operations in ``send_order``, which can reach nothing but
``/trader/v1/accounts/{hash}/orders[/{id}]``. Writes are never retried, because
a request that dropped in transit may already have been accepted. The only
other POST in the package is the OAuth token call in oauth.py.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any, Awaitable, Callable

import httpx

from .errors import SchwabAPIError, SessionExpired
from .formatting import mask_text
from .oauth import TokenManager

log = logging.getLogger("schwab_mcp")

API_BASE = "https://api.schwabapi.com"
ALLOWED_PREFIXES = ("/trader/v1/accounts", "/marketdata/v1/")
RETRYABLE_5XX = (500, 502, 503, 504)
ORDER_PATH_RE = re.compile(r"^/trader/v1/accounts/[A-Za-z0-9_-]+/orders(/\d+)?$")
ORDER_METHODS = ("POST", "PUT", "DELETE", "GET")


def _schwab_message(response: httpx.Response) -> str:
    """Best-effort short error text from a Schwab error body, account numbers masked."""
    try:
        body = response.json()
    except ValueError:
        return ""
    parts: list[str] = []
    if isinstance(body, dict):
        for key in ("message", "error_description", "error"):
            if isinstance(body.get(key), str):
                parts.append(body[key])
        for err in body.get("errors", []) if isinstance(body.get("errors"), list) else []:
            if isinstance(err, dict):
                parts.append(str(err.get("detail") or err.get("title") or ""))
    return mask_text("; ".join(p for p in parts if p))[:300]


class SchwabClient:
    def __init__(
        self,
        tokens: TokenManager,
        http: httpx.AsyncClient | None = None,
        max_429_retries: int = 4,
        max_5xx_retries: int = 2,
        backoff_base: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.tokens = tokens
        self.http = http or httpx.AsyncClient(base_url=API_BASE, timeout=20.0)
        self.max_429_retries = max_429_retries
        self.max_5xx_retries = max_5xx_retries
        self.backoff_base = backoff_base
        self.sleep = sleep

    def _delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
        return self.backoff_base * (2**attempt) + random.uniform(0, self.backoff_base)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not path.startswith(ALLOWED_PREFIXES) or "/orders" in path or "previewOrder" in path:
            raise SchwabAPIError(f"Refusing to call non read-only path {path!r}.")
        return (await self._send("GET", path, params=params)).json()

    async def get_orders(self, account_hash: str, params: dict[str, Any] | None = None) -> Any:
        return (await self._send("GET", self._order_path(account_hash, None), params=params)).json()

    async def get_order(self, account_hash: str, order_id: str) -> Any:
        return (await self._send("GET", self._order_path(account_hash, order_id))).json()

    async def send_order(
        self, method: str, account_hash: str, order_id: str | None = None, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Place (POST), replace (PUT) or cancel (DELETE) an order. Never retried."""
        if method not in ("POST", "PUT", "DELETE"):
            raise SchwabAPIError(f"Refusing unsupported order method {method!r}.")
        if (method == "POST") != (order_id is None):
            raise SchwabAPIError("Order id is required to replace or cancel, and not allowed when placing.")
        response = await self._send(method, self._order_path(account_hash, order_id), json_body=body, write=True)
        location = response.headers.get("Location", "")
        new_id = location.rstrip("/").rsplit("/", 1)[-1] if "/orders/" in location else None
        return {"http_status": response.status_code, "order_id": new_id or order_id}

    @staticmethod
    def _order_path(account_hash: str, order_id: str | None) -> str:
        path = f"/trader/v1/accounts/{account_hash}/orders" + (f"/{order_id}" if order_id else "")
        if not ORDER_PATH_RE.match(path):
            raise SchwabAPIError("Refusing to call a malformed order path.")
        return path

    async def _send(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        write: bool = False,
    ) -> httpx.Response:
        hits_429 = hits_5xx = 0
        refreshed_after_401 = False
        unknown = (
            "The order outcome is UNKNOWN: do not resend. Call get_orders to check whether it went through."
        )
        while True:
            token = await self.tokens.access_token(self.http, force_refresh=False)
            try:
                response = await self.http.request(
                    method,
                    API_BASE + path,
                    params=params,
                    json=json_body,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                )
            except httpx.TransportError as exc:
                if write:
                    raise SchwabAPIError(f"Connection to Schwab failed ({type(exc).__name__}). " + unknown) from None
                if hits_5xx < self.max_5xx_retries:
                    hits_5xx += 1
                    await self.sleep(self._delay(hits_5xx, None))
                    continue
                raise SchwabAPIError(f"Could not reach Schwab ({type(exc).__name__}). Check your connection.") from None

            status = response.status_code
            if status == 200 or (write and status in (201, 204)):
                return response
            if status == 429:
                if write:
                    raise SchwabAPIError("Schwab rate limit hit (HTTP 429). The order was not accepted; wait and retry.", 429)
                if hits_429 < self.max_429_retries:
                    delay = self._delay(hits_429, response.headers.get("Retry-After"))
                    hits_429 += 1
                    log.warning("Schwab rate limit hit; retrying in %.1fs", delay)
                    await self.sleep(delay)
                    continue
                raise SchwabAPIError(
                    "Schwab rate limit reached (HTTP 429) and retries were exhausted. Wait a minute and try again.",
                    429,
                )
            if status in RETRYABLE_5XX and not write and hits_5xx < self.max_5xx_retries:
                hits_5xx += 1
                await self.sleep(self._delay(hits_5xx, None))
                continue
            if status == 401:
                if not refreshed_after_401:
                    refreshed_after_401 = True
                    await self.tokens.access_token(self.http, force_refresh=True)
                    continue
                raise SessionExpired("Schwab rejected the access token (HTTP 401)")
            detail = _schwab_message(response)
            suffix = f" Schwab said: {detail}" if detail else ""
            if status == 403:
                raise SchwabAPIError(
                    "Schwab denied access (HTTP 403). Check that your app has the 'Accounts and Trading Production' "
                    "and 'Market Data Production' products and that this account is linked." + suffix,
                    403,
                )
            if status == 404:
                raise SchwabAPIError("Not found (HTTP 404)." + suffix, 404)
            if status == 400:
                raise SchwabAPIError("Schwab rejected the request (HTTP 400)." + suffix, 400)
            if status >= 500:
                if write:
                    raise SchwabAPIError(f"Schwab returned HTTP {status}. " + unknown + suffix, status)
                raise SchwabAPIError(
                    f"Schwab is having trouble (HTTP {status}) and retries were exhausted. Try again later.", status
                )
            raise SchwabAPIError(f"Unexpected response from Schwab (HTTP {status})." + suffix, status)

    async def aclose(self) -> None:
        await self.http.aclose()
