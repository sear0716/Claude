"""Shared fixtures. Nothing here touches the network or needs real credentials."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
import respx

from schwab_mcp import server
from schwab_mcp.client import API_BASE, SchwabClient
from schwab_mcp.config import Settings
from schwab_mcp.oauth import TokenManager
from schwab_mcp.tokens import FileTokenStore

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_ACCESS = "FAKE-ACCESS-TOKEN-abc123"
FAKE_REFRESH = "FAKE-REFRESH-TOKEN-xyz789"
FAKE_SECRET = "FAKE-APP-SECRET-000"
FULL_ACCOUNT_NUMBERS = ("12345678", "87654321")


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        app_key="FAKE-APP-KEY",
        app_secret=FAKE_SECRET,
        callback_url="https://127.0.0.1:8182",
        token_path=tmp_path / "tok" / "schwab_token.json",
    )


def make_token(now: float, *, expires_in: float = 1800, age: float = 0) -> dict:
    return {
        "access_token": FAKE_ACCESS,
        "refresh_token": FAKE_REFRESH,
        "token_type": "Bearer",
        "scope": "api",
        "expires_at": now + expires_in,
        "refresh_token_issued_at": now - age,
    }


@pytest.fixture
def store(settings) -> FileTokenStore:
    s = FileTokenStore(settings.token_path)
    s.save(make_token(time.time()))
    return s


@pytest.fixture
def api():
    with respx.mock(base_url=API_BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def client(settings, store, sleeps) -> SchwabClient:
    async def fake_sleep(seconds):
        sleeps.append(seconds)

    return SchwabClient(
        TokenManager(settings, store), http=httpx.AsyncClient(), sleep=fake_sleep
    )


@pytest.fixture
def backend(client, monkeypatch):
    b = server.Backend(client)
    monkeypatch.setattr(server, "backend", b)
    return b


def call(name: str, **arguments):
    """Call a tool through an in-process MCP client session, the same way Claude would."""
    from mcp import Client

    async def go():
        async with Client(server.mcp) as c:
            return await c.call_tool(name, arguments)

    return asyncio.run(go())


def payload(result) -> dict:
    assert not result.is_error, result.content
    return result.structured_content


def error_text(result) -> str:
    assert result.is_error, result.structured_content
    return " ".join(getattr(c, "text", "") for c in result.content)
