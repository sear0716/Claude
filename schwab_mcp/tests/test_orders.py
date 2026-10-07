"""Order tools: disabled by default, preview then confirm, caps, no retries on writes. All mocked."""

import json

import httpx
import pytest

from .conftest import FAKE_ACCESS, call, error_text, load, payload

PLACE = {"symbol": "AAPL", "instruction": "BUY", "quantity": 10, "order_type": "LIMIT", "limit_price": 150.5}
ORDERS = "/trader/v1/accounts/HASH_AAA111/orders"


@pytest.fixture
def mocks(api, trading):
    api.get("/trader/v1/accounts/accountNumbers").respond(json=load("account_numbers.json"))
    api.get(ORDERS).respond(json=load("orders.json"))
    api.get(f"{ORDERS}/9001").respond(json=load("orders.json")[0])
    return api


def preview_then_confirm(name, args):
    p = payload(call(name, account="5678", **args))
    assert p["status"].startswith("PREVIEW")
    return p, call(name, account="5678", confirm_token=p["confirm_token"], **args)


def test_disabled_by_default(api, backend):
    for name, args in (("place_order", PLACE), ("cancel_order", {"order_id": "9001"})):
        assert "Trading is disabled" in error_text(call(name, **args))
    assert not api.calls  # not even a read happened


def test_place_preview_sends_nothing(mocks):
    post = mocks.post(ORDERS).respond(201)
    p = payload(call("place_order", account="5678", **PLACE))
    assert post.call_count == 0
    assert p["order"] == "BUY 10 AAPL LIMIT limit 150.5 DAY / NORMAL"
    assert p["estimated_value"] == 1505.0 and p["account"] == "****5678"
    assert p["confirm_token"]


def test_place_confirm_sends_expected_body(mocks):
    post = mocks.post(ORDERS).respond(201, headers={"Location": f"https://api.schwabapi.com{ORDERS}/777"})
    _, result = preview_then_confirm("place_order", PLACE)
    out = payload(result)
    assert out["status"] == "SENT" and out["order_id"] == "777"
    req = post.calls.last.request
    assert req.headers["Authorization"] == f"Bearer {FAKE_ACCESS}"
    assert json.loads(req.content) == {
        "orderType": "LIMIT",
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "price": "150.5",
        "orderLegCollection": [
            {"instruction": "BUY", "quantity": 10, "instrument": {"symbol": "AAPL", "assetType": "EQUITY"}}
        ],
    }


def test_token_single_use_and_bound_to_arguments(mocks):
    post = mocks.post(ORDERS).respond(201)
    p = payload(call("place_order", account="5678", **PLACE))
    changed = {**PLACE, "quantity": 11}
    assert "differ" in error_text(call("place_order", account="5678", confirm_token=p["confirm_token"], **changed))
    # the failed attempt burned the token
    assert "unknown, expired or already used" in error_text(
        call("place_order", account="5678", confirm_token=p["confirm_token"], **PLACE)
    )
    assert post.call_count == 0


def test_token_replay_after_success(mocks):
    mocks.post(ORDERS).respond(201)
    p, result = preview_then_confirm("place_order", PLACE)
    assert not result.is_error
    assert call("place_order", account="5678", confirm_token=p["confirm_token"], **PLACE).is_error


def test_expired_token(mocks, trading):
    clock = [1000.0]
    trading.confirm.clock = lambda: clock[0]
    p = payload(call("place_order", account="5678", **PLACE))
    clock[0] += 301
    assert "expired" in error_text(call("place_order", account="5678", confirm_token=p["confirm_token"], **PLACE))


def test_value_cap(mocks):
    big = {**PLACE, "quantity": 100}  # 100 x 150.5 = 15,050
    assert "SCHWAB_MAX_ORDER_VALUE" in error_text(call("place_order", account="5678", **big))


def test_option_counts_x100(mocks):
    args = {"symbol": "AAPL 260117C00150000", "instruction": "BUY_TO_OPEN", "quantity": 5, "order_type": "LIMIT",
            "limit_price": 12, "asset_type": "OPTION"}
    assert "SCHWAB_MAX_ORDER_VALUE" in error_text(call("place_order", account="5678", **args))  # $6,000
    p = payload(call("place_order", account="5678", **{**args, "quantity": 2}))
    assert p["estimated_value"] == 2400.0 and "AAPL  260117C00150000" in p["order"]


def test_market_order_sized_from_quote(mocks):
    mocks.get("/marketdata/v1/quotes").respond(json={"AAPL": {"quote": {"askPrice": 200.0, "lastPrice": 199.0}}})
    args = {"symbol": "AAPL", "instruction": "BUY", "quantity": 10, "order_type": "MARKET"}
    assert payload(call("place_order", account="5678", **args))["estimated_value"] == 2000.0
    assert "SCHWAB_MAX_ORDER_VALUE" in error_text(call("place_order", account="5678", **{**args, "quantity": 30}))


def test_market_order_without_price_refused(mocks):
    mocks.get("/marketdata/v1/quotes").respond(json={})
    args = {"symbol": "AAPL", "instruction": "BUY", "quantity": 1, "order_type": "MARKET"}
    assert "limit order" in error_text(call("place_order", account="5678", **args))


@pytest.mark.parametrize(
    "change,text",
    [
        ({"quantity": 0}, "quantity"),
        ({"quantity": 1.5}, "quantity"),
        ({"instruction": "YOLO"}, "instruction"),
        ({"order_type": "LIMIT", "limit_price": None}, "need limit_price"),
        ({"order_type": "MARKET"}, "not used"),
        ({"order_type": "STOP"}, "need stop_price"),
        ({"limit_price": -1}, "positive"),
        ({"limit_price": 1.23456}, "decimal"),
        ({"symbol": "$SPX"}, "plain tickers"),
        ({"duration": "FOREVER"}, "duration"),
        ({"asset_type": "OPTION", "instruction": "BUY_TO_OPEN"}, "OCC"),
    ],
)
def test_validation(mocks, change, text):
    post = mocks.post(ORDERS).respond(201)
    result = call("place_order", account="5678", **{**PLACE, **change})
    assert text in error_text(result)
    assert post.call_count == 0


def test_replace_flow(mocks):
    put = mocks.put(f"{ORDERS}/9001").respond(201, headers={"Location": f"https://x{ORDERS}/9002"})
    args = {"order_id": "9001", **PLACE, "limit_price": 151}
    p, result = preview_then_confirm("replace_order", args)
    assert p["existing_order"]["order_id"] == "9001" and p["existing_order"]["price"] == 150.5
    assert payload(result)["order_id"] == "9002"
    assert json.loads(put.calls.last.request.content)["price"] == "151"


def test_cancel_flow(mocks):
    delete = mocks.delete(f"{ORDERS}/9001").respond(200)
    p, result = preview_then_confirm("cancel_order", {"order_id": "9001"})
    assert p["existing_order"]["status"] == "WORKING"
    assert payload(result)["status"] == "CANCEL SENT"
    assert delete.call_count == 1


def test_cancel_rejects_bad_id(mocks):
    assert "numeric" in error_text(call("cancel_order", order_id="9001/../x", account="5678"))


def test_write_not_retried_on_5xx(mocks, sleeps):
    post = mocks.post(ORDERS).respond(503)
    _, result = preview_then_confirm("place_order", PLACE)
    assert "UNKNOWN" in error_text(result) and "get_orders" in error_text(result)
    assert post.call_count == 1 and not sleeps


def test_write_not_retried_on_network_error(mocks):
    post = mocks.post(ORDERS).mock(side_effect=httpx.ConnectError("boom"))
    _, result = preview_then_confirm("place_order", PLACE)
    assert "UNKNOWN" in error_text(result)
    assert post.call_count == 1


def test_schwab_rejection_is_readable(mocks):
    mocks.post(ORDERS).respond(400, json={"message": "Insufficient buying power for account 12345678"})
    _, result = preview_then_confirm("place_order", PLACE)
    text = error_text(result)
    assert "Insufficient buying power" in text and "12345678" not in text


def test_get_orders_shape(mocks):
    out = payload(call("get_orders", account="5678", status="working"))
    assert out["orders"][0]["order_id"] == "9001"
    assert out["orders"][0]["legs"][0]["symbol"] == "AAPL"
    assert "accountNumber" not in json.dumps(out)
    assert mocks.calls.last.request.url.params["status"] == "WORKING"


def test_settings_default_off_and_cap(monkeypatch, tmp_path):
    from schwab_mcp.config import load_settings

    env = {"SCHWAB_APP_KEY": "k", "SCHWAB_APP_SECRET": "s", "SCHWAB_CALLBACK_URL": "https://127.0.0.1:8182",
           "SCHWAB_ENV_FILE": str(tmp_path / "none.env")}
    for k, val in env.items():
        monkeypatch.setenv(k, val)
    monkeypatch.delenv("SCHWAB_ENABLE_TRADING", raising=False)
    monkeypatch.delenv("SCHWAB_MAX_ORDER_VALUE", raising=False)
    s = load_settings()
    assert s.trading_enabled is False and s.max_order_value == 5000.0
    monkeypatch.setenv("SCHWAB_ENABLE_TRADING", "yes")  # only the exact word "true" enables it
    assert load_settings().trading_enabled is False
    monkeypatch.setenv("SCHWAB_ENABLE_TRADING", "TRUE")
    monkeypatch.setenv("SCHWAB_MAX_ORDER_VALUE", "250")
    s = load_settings()
    assert s.trading_enabled is True and s.max_order_value == 250.0
