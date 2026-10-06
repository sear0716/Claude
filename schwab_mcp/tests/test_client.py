import asyncio

import httpx
import pytest

from schwab_mcp.errors import SchwabAPIError, SessionExpired
from schwab_mcp.oauth import TOKEN_URL

from .conftest import FAKE_ACCESS, call, error_text


def run(coro):
    return asyncio.run(coro)


def test_sends_bearer_token(api, client):
    route = api.get("/marketdata/v1/markets").respond(json={})
    run(client.get("/marketdata/v1/markets", {"markets": "equity"}))
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {FAKE_ACCESS}"
    assert route.calls.last.request.method == "GET"


def test_429_backs_off_then_succeeds(api, client, sleeps):
    api.get("/marketdata/v1/quotes").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(429),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    assert run(client.get("/marketdata/v1/quotes")) == {"ok": True}
    assert sleeps[0] == 3.0  # honours Retry-After
    assert 2.0 <= sleeps[1] <= 3.0  # exponential: base * 2**1 + jitter


def test_429_exhausted(api, client, sleeps):
    api.get("/marketdata/v1/quotes").respond(429)
    with pytest.raises(SchwabAPIError, match="rate limit"):
        run(client.get("/marketdata/v1/quotes"))
    assert len(sleeps) == 4


def test_5xx_retried_then_readable_error(api, client, sleeps):
    route = api.get("/marketdata/v1/quotes").respond(503)
    with pytest.raises(SchwabAPIError, match=r"HTTP 503.*Try again later"):
        run(client.get("/marketdata/v1/quotes"))
    assert route.call_count == 3


def test_transport_error(api, client):
    api.get("/marketdata/v1/quotes").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(SchwabAPIError, match="Could not reach Schwab"):
        run(client.get("/marketdata/v1/quotes"))


def test_403_is_explained(api, client):
    api.get("/trader/v1/accounts").respond(403, json={"message": "Forbidden for account 12345678"})
    with pytest.raises(SchwabAPIError) as err:
        run(client.get("/trader/v1/accounts"))
    text = str(err.value)
    assert "HTTP 403" in text and "Accounts and Trading Production" in text
    assert "12345678" not in text and "****5678" in text


def test_400_includes_schwab_message(api, client):
    api.get("/marketdata/v1/chains").respond(400, json={"errors": [{"detail": "Invalid strikeCount"}]})
    with pytest.raises(SchwabAPIError, match="Invalid strikeCount"):
        run(client.get("/marketdata/v1/chains"))


def test_401_refreshes_once_and_retries(api, client):
    api.post(TOKEN_URL).respond(json={"access_token": "NEW-ACCESS", "refresh_token": "FAKE-REFRESH-TOKEN-xyz789",
                                      "expires_in": 1800})
    route = api.get("/marketdata/v1/quotes").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json={"ok": True})]
    )
    assert run(client.get("/marketdata/v1/quotes")) == {"ok": True}
    assert route.calls.last.request.headers["Authorization"] == "Bearer NEW-ACCESS"


def test_401_after_refresh_is_session_expired(api, client):
    api.post(TOKEN_URL).respond(json={"access_token": "NEW-ACCESS", "expires_in": 1800,
                                      "refresh_token": "FAKE-REFRESH-TOKEN-xyz789"})
    api.get("/marketdata/v1/quotes").respond(401)
    with pytest.raises(SessionExpired, match="run auth.py"):
        run(client.get("/marketdata/v1/quotes"))


@pytest.mark.parametrize("path", ["/trader/v1/accounts/HASH/orders", "/trader/v1/orders",
                                  "/trader/v1/accounts/HASH/previewOrder", "/trader/v1/userPreference"])
def test_refuses_non_read_only_paths(api, client, path):
    with pytest.raises(SchwabAPIError, match="Refusing"):
        run(client.get(path))
    assert not api.calls


def test_rate_limit_surfaces_as_tool_error(api, backend):
    api.get("/marketdata/v1/quotes").respond(429)
    assert "rate limit" in error_text(call("get_quote", symbols=["AAPL"]))
