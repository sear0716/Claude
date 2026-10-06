import asyncio
import json
import os
import stat
import time

import httpx
import pytest

from schwab_mcp import auth as auth_script
from schwab_mcp.config import load_settings
from schwab_mcp.errors import ConfigError, SchwabAPIError
from schwab_mcp.oauth import (
    REFRESH_TOKEN_LIFETIME,
    TOKEN_URL,
    TokenManager,
    build_authorize_url,
    code_from_redirect,
    exchange_code,
    normalize_token,
)
from schwab_mcp.tokens import FileTokenStore, KeyringTokenStore

from .conftest import FAKE_REFRESH, FAKE_SECRET, call, error_text, make_token, payload

EXPIRED_MSG = "Schwab session expired, run auth.py to re-authenticate"


def test_authorize_url(settings):
    url = build_authorize_url(settings, "st8")
    assert url.startswith("https://api.schwabapi.com/v1/oauth/authorize?")
    assert "client_id=FAKE-APP-KEY" in url and "redirect_uri=https%3A%2F%2F127.0.0.1%3A8182" in url
    assert "state=st8" in url and "response_type=code" in url
    assert FAKE_SECRET not in url


def test_code_from_redirect():
    url = "https://127.0.0.1:8182/?code=C0.abc%40&session=s1&state=st8"
    assert code_from_redirect(url, "st8") == "C0.abc@"
    with pytest.raises(SchwabAPIError, match="State mismatch"):
        code_from_redirect(url, "other")
    with pytest.raises(SchwabAPIError, match="no \\?code="):
        code_from_redirect("https://127.0.0.1:8182/", "st8")


def test_exchange_code_uses_basic_auth_and_drops_id_token(settings):
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"access_token": "A", "refresh_token": "R", "expires_in": 1800,
                                         "token_type": "Bearer", "scope": "api", "id_token": "JWT"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        token = exchange_code(settings, "C0.abc@", http)
    assert seen["auth"].startswith("Basic ")
    assert "grant_type=authorization_code" in seen["body"] and "code=C0.abc%40" in seen["body"]
    assert "id_token" not in token
    assert token["expires_at"] - token["refresh_token_issued_at"] == 1800


def test_normalize_keeps_refresh_clock_when_refresh_token_unchanged():
    prev = {"refresh_token": "R", "refresh_token_issued_at": 1000.0}
    tok = normalize_token({"access_token": "A2", "expires_in": 1800}, now=5000.0, previous=prev)
    assert tok["refresh_token"] == "R" and tok["refresh_token_issued_at"] == 1000.0
    tok = normalize_token({"access_token": "A2", "refresh_token": "R2"}, now=5000.0, previous=prev)
    assert tok["refresh_token_issued_at"] == 5000.0


def test_file_store_is_0600(settings, tmp_path):
    store = FileTokenStore(settings.token_path)
    store.save({"access_token": "x"})
    assert stat.S_IMODE(os.stat(settings.token_path).st_mode) == 0o600
    os.chmod(settings.token_path, 0o644)
    store.load()
    assert stat.S_IMODE(os.stat(settings.token_path).st_mode) == 0o600
    assert not list(settings.token_path.parent.glob("*.tmp"))


def test_expired_access_token_is_refreshed_and_saved(api, settings, client, store):
    store.save(make_token(time.time(), expires_in=-10, age=3600))
    refresh = api.post(TOKEN_URL).respond(json={"access_token": "NEW-ACCESS", "refresh_token": FAKE_REFRESH,
                                                "expires_in": 1800})
    api.get("/marketdata/v1/markets").respond(json={})
    asyncio.run(client.get("/marketdata/v1/markets"))
    assert refresh.call_count == 1
    assert "grant_type=refresh_token" in refresh.calls.last.request.content.decode()
    saved = json.loads(settings.token_path.read_text())
    assert saved["access_token"] == "NEW-ACCESS"
    assert stat.S_IMODE(os.stat(settings.token_path).st_mode) == 0o600
    # The 7-day clock is not reset by a refresh.
    assert time.time() - saved["refresh_token_issued_at"] >= 3599


def test_valid_access_token_is_not_refreshed(api, client):
    refresh = api.post(TOKEN_URL)
    api.get("/marketdata/v1/markets").respond(json={})
    asyncio.run(client.get("/marketdata/v1/markets"))
    assert not refresh.called


def test_refresh_token_older_than_7_days_gives_clear_error(api, backend, store):
    store.save(make_token(time.time(), age=REFRESH_TOKEN_LIFETIME + 60))
    route = api.get("/marketdata/v1/markets")
    msg = error_text(call("get_market_hours"))
    assert EXPIRED_MSG in msg
    assert not route.called


def test_new_login_is_picked_up_without_restart(api, backend, store):
    store.save(make_token(time.time(), age=REFRESH_TOKEN_LIFETIME + 60))
    api.get("/marketdata/v1/markets").respond(json={})
    assert EXPIRED_MSG in error_text(call("get_market_hours"))
    store.save(make_token(time.time()))  # user re-runs auth.py
    payload(call("get_market_hours"))


def test_rejected_refresh_gives_clear_error(api, backend, store):
    store.save(make_token(time.time(), expires_in=-10, age=3600))
    api.post(TOKEN_URL).respond(400, json={"error": "invalid_grant"})
    assert EXPIRED_MSG in error_text(call("get_market_hours"))


def test_missing_token_gives_clear_error(api, backend, settings):
    settings.token_path.unlink()
    assert EXPIRED_MSG in error_text(call("get_market_hours"))


def test_missing_config_is_a_readable_tool_error(monkeypatch, tmp_path):
    from schwab_mcp import server

    for name in ("SCHWAB_APP_KEY", "SCHWAB_APP_SECRET", "SCHWAB_CALLBACK_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SCHWAB_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setattr(server, "backend", server.Backend())
    msg = error_text(call("get_market_hours"))
    assert "Missing SCHWAB_APP_KEY" in msg


def test_load_settings_from_env_file(monkeypatch, tmp_path):
    for name in ("SCHWAB_APP_KEY", "SCHWAB_APP_SECRET", "SCHWAB_CALLBACK_URL", "TOKEN_PATH", "TOKEN_STORE"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text("SCHWAB_APP_KEY=k\nSCHWAB_APP_SECRET=s\nSCHWAB_CALLBACK_URL=https://127.0.0.1:8182\n"
                   "TOKEN_PATH=~/tok.json\n")
    monkeypatch.setenv("SCHWAB_ENV_FILE", str(env))
    s = load_settings()
    assert s.app_key == "k" and s.token_path.name == "tok.json" and "~" not in str(s.token_path)
    assert "s" not in repr(s).split("callback_url")[0].replace("Settings", "")  # secret not in repr
    monkeypatch.setenv("SCHWAB_CALLBACK_URL", "http://insecure")
    with pytest.raises(ConfigError, match="https"):
        load_settings()


def test_keyring_store(monkeypatch):
    import keyring
    from keyring.backend import KeyringBackend

    class MemoryKeyring(KeyringBackend):
        priority = 1
        data: dict = {}

        def get_password(self, service, username):
            return self.data.get((service, username))

        def set_password(self, service, username, password):
            self.data[(service, username)] = password

        def delete_password(self, service, username):
            self.data.pop((service, username), None)

    keyring.set_keyring(MemoryKeyring())
    store = KeyringTokenStore()
    assert store.load() is None
    store.save({"access_token": "a"})
    assert store.load() == {"access_token": "a"}


def test_auth_status(settings, store, monkeypatch, capsys):
    monkeypatch.setattr(auth_script, "load_settings", lambda: settings)
    assert auth_script.main(["--status"]) == 0
    out = capsys.readouterr().out
    assert "Session valid until" in out and "FAKE" not in out


def test_auth_login_flow(settings, monkeypatch, capsys):
    """Full auth.py run with the browser, input() and Schwab all faked."""
    monkeypatch.setattr(auth_script, "load_settings", lambda: settings)
    opened = {}
    monkeypatch.setattr(auth_script.webbrowser, "open", lambda url: opened.setdefault("url", url) and True)
    monkeypatch.setattr(auth_script.secrets, "token_urlsafe", lambda n: "STATE")
    monkeypatch.setattr("builtins.input", lambda prompt="": "https://127.0.0.1:8182/?code=CODE%40&state=STATE")

    def handler(request: httpx.Request):
        if request.url.path == "/v1/oauth/token":
            return httpx.Response(200, json={"access_token": "A", "refresh_token": "R", "expires_in": 1800})
        assert request.method == "GET"
        return httpx.Response(200, json=[{"accountNumber": "12345678", "hashValue": "H"}])

    real_client = httpx.Client
    monkeypatch.setattr(auth_script.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    assert auth_script.main([]) == 0
    out = capsys.readouterr().out
    assert "Verified: 1 linked account(s): ****5678" in out
    assert "12345678" not in out and opened["url"].endswith("state=STATE")
    saved = json.loads(settings.token_path.read_text())
    assert saved["refresh_token"] == "R"
    assert stat.S_IMODE(os.stat(settings.token_path).st_mode) == 0o600
