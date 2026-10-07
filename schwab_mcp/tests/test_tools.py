import httpx
import pytest

from .conftest import call, error_text, load, payload


@pytest.fixture
def accounts_api(api, backend):
    api.get("/trader/v1/accounts/accountNumbers").respond(json=load("account_numbers.json"))
    api.get("/trader/v1/accounts").respond(json=load("accounts.json"))
    return api


def test_get_accounts_masks_numbers_and_returns_hashes(accounts_api):
    out = payload(call("get_accounts"))
    assert out["accounts"] == [
        {"account": "****5678", "account_hash": "HASH_AAA111", "type": "MARGIN"},
        {"account": "****4321", "account_hash": "HASH_BBB222", "type": "CASH"},
    ]


def test_account_summary_all_accounts(accounts_api):
    out = payload(call("get_account_summary"))
    margin, cash = out["accounts"]
    assert margin["account"] == "****5678"
    assert margin["account_hash"] == "HASH_AAA111"
    assert margin["buying_power"] == 36000.0
    assert margin["equity"] == 101000.25
    assert margin["cash_balance"] == 20000.0
    assert cash["cash_balance"] == 5000.0  # falls back to totalCash
    assert cash["buying_power"] == 5000.0  # cash account: cashAvailableForTrading
    assert "current_balances" not in margin


def test_account_summary_by_last4_with_detail(api, backend):
    api.get("/trader/v1/accounts/accountNumbers").respond(json=load("account_numbers.json"))
    single = load("accounts.json")[1]
    route = api.get("/trader/v1/accounts/HASH_BBB222").respond(json=single)
    out = payload(call("get_account_summary", account="4321", detail=True))
    assert route.called
    acct = out["accounts"][0]
    assert acct["account"] == "****4321"
    assert acct["current_balances"]["unsettledCash"] == 200.0


def test_positions(api, backend):
    api.get("/trader/v1/accounts/accountNumbers").respond(json=load("account_numbers.json"))
    route = api.get("/trader/v1/accounts/HASH_AAA111", params={"fields": "positions"}).respond(
        json=load("account_positions.json")
    )
    out = payload(call("get_positions", account="HASH_AAA111"))
    assert route.called
    assert out["account"] == "****5678"
    aapl = out["positions"][0]
    assert aapl == {
        "symbol": "AAPL",
        "description": "APPLE INC",
        "asset_type": "EQUITY",
        "quantity": 100.0,
        "average_price": 150.0,
        "cost_basis": 15000.0,
        "market_value": 18000.0,
        "day_pl": 120.0,
        "day_pl_pct": 0.67,
        "total_pl": 3000.0,
        "total_pl_pct": 20.0,
    }
    option = out["positions"][1]
    assert option["cost_basis"] == 1100.0  # market value minus open P/L, multiplier already included
    assert out["totals"] == {"market_value": 18600.0, "day_pl": 80.0, "total_pl": 2500.0}


def test_positions_requires_account_choice_when_several(api, backend):
    api.get("/trader/v1/accounts/accountNumbers").respond(json=load("account_numbers.json"))
    msg = error_text(call("get_positions"))
    assert "****5678" in msg and "****4321" in msg
    assert "12345678" not in msg


def test_unknown_account(api, backend):
    api.get("/trader/v1/accounts/accountNumbers").respond(json=load("account_numbers.json"))
    assert "No linked account" in error_text(call("get_positions", account="9999"))


def test_get_quote(api, backend):
    route = api.get("/marketdata/v1/quotes").respond(json=load("quotes.json"))
    out = payload(call("get_quote", symbols=["aapl", "zzzzz"], include_fundamentals=True))
    sent = route.calls.last.request.url.params
    assert sent["symbols"] == "AAPL,ZZZZZ"
    assert sent["fields"] == "quote,reference,fundamental"
    q = out["quotes"][0]
    assert q["symbol"] == "AAPL" and q["last"] == 180.05 and q["realtime"] is True
    assert q["quote_time"] == "2025-01-02T16:00:00Z"
    assert q["fundamentals"]["pe_ratio"] == 29.5
    assert out["not_found"] == ["ZZZZZ"]


@pytest.mark.parametrize("bad", [["AAPL;DROP"], [""], ["A" * 40], []])
def test_get_quote_rejects_bad_symbols_without_calling_api(api, backend, bad):
    route = api.get("/marketdata/v1/quotes")
    assert error_text(call("get_quote", symbols=bad))
    assert not route.called


def test_get_price_history(api, backend):
    route = api.get("/marketdata/v1/pricehistory").respond(json=load("price_history.json"))
    out = payload(call("get_price_history", symbol="AAPL", period_type="year", period=1,
                       frequency_type="daily", max_candles=2))
    sent = route.calls.last.request.url.params
    assert (sent["periodType"], sent["period"], sent["frequencyType"], sent["frequency"]) == (
        "year", "1", "daily", "1")
    assert out["columns"] == ["time", "open", "high", "low", "close", "volume"]
    assert out["count"] == 2
    assert out["candles"][-1] == ["2025-01-01T05:00:00Z", 180.1, 182.2, 179.0, 181.9, 49000000]
    assert "truncated" in out


def test_price_history_date_range_is_sent_as_epoch_ms(api, backend):
    route = api.get("/marketdata/v1/pricehistory").respond(json=load("price_history.json"))
    payload(call("get_price_history", symbol="AAPL", period_type="month", period=None,
                 start_date="2024-12-01", end_date="2024-12-31"))
    sent = route.calls.last.request.url.params
    assert sent["startDate"] == "1733011200000"
    assert sent["endDate"] == "1735689599999"
    assert "period" not in sent


def test_price_history_sends_end_date_now_so_today_is_included(api, backend):
    import time

    route = api.get("/marketdata/v1/pricehistory").respond(json=load("price_history.json"))
    before = int(time.time() * 1000)
    payload(call("get_price_history", symbol="SPY", period_type="day", period=1, frequency_type="minute",
                 frequency=5))
    sent = route.calls.last.request.url.params
    assert sent["period"] == "1" and "startDate" not in sent
    assert before <= int(sent["endDate"]) <= int(time.time() * 1000)


def test_index_quote_omits_all_zero_fundamentals(api, backend):
    api.get("/marketdata/v1/quotes").respond(json={
        "$SPX": {"assetMainType": "INDEX", "symbol": "$SPX", "realtime": True,
                 "quote": {"lastPrice": 5800.5},
                 "fundamental": {"peRatio": 0, "eps": 0.0, "divYield": 0, "divAmount": 0, "avg10DaysVolume": 0}}})
    q = payload(call("get_quote", symbols=["$SPX"], include_fundamentals=True))["quotes"][0]
    assert q["last"] == 5800.5 and "fundamentals" not in q


@pytest.mark.parametrize(
    "kwargs",
    [
        {"period_type": "day", "frequency_type": "daily"},  # day needs minute candles
        {"period_type": "month", "period": 4},
        {"period_type": "day", "frequency_type": "minute", "frequency": 7},
        {"period_type": "decade"},
        {"start_date": "2024-12-31", "end_date": "2024-12-01"},
        {"start_date": "2999-01-01"},
        {"start_date": "12/01/2024"},
    ],
)
def test_price_history_validation(api, backend, kwargs):
    route = api.get("/marketdata/v1/pricehistory")
    assert error_text(call("get_price_history", symbol="AAPL", **kwargs))
    assert not route.called


def test_option_chain_trimmed_by_default(api, backend):
    route = api.get("/marketdata/v1/chains").respond(json=load("option_chain.json"))
    out = payload(call("get_option_chain", symbol="AAPL", from_date="2025-01-01"))
    sent = route.calls.last.request.url.params
    assert sent["strikeCount"] == "10" and sent["contractType"] == "ALL"
    assert sent["fromDate"] == "2025-01-01" and sent["toDate"] == "2025-03-02"
    assert out["total_expirations"] == 4
    assert [e["date"] for e in out["expirations"]] == ["2025-01-10", "2025-01-17", "2025-01-24"]
    first = out["expirations"][0]
    assert first["dte"] == 7
    assert [row[0] for row in first["calls"]] == [175.0, 180.0, 185.0, 190.0]  # sorted strikes
    assert len(first["calls"][0]) == len(out["columns"])
    assert "truncated" in out


def test_option_chain_widened_and_full(api, backend):
    route = api.get("/marketdata/v1/chains").respond(json=load("option_chain.json"))
    out = payload(call("get_option_chain", symbol="AAPL", contract_type="call", strike_count=40,
                       max_expirations=10, full=True, from_date="2025-01-01", to_date="2025-06-30"))
    sent = route.calls.last.request.url.params
    assert sent["contractType"] == "CALL" and sent["strikeCount"] == "40"
    assert len(out["expirations"]) == 4 and "truncated" not in out
    contract = out["expirations"][0]["calls"][0]
    assert contract["symbol"].startswith("AAPL") and contract["multiplier"] == 100.0


def test_option_chain_validation(api, backend):
    route = api.get("/marketdata/v1/chains")
    assert "contract_type" in error_text(call("get_option_chain", symbol="AAPL", contract_type="STRADDLE"))
    assert "strike_count" in error_text(call("get_option_chain", symbol="AAPL", strike_count=0))
    assert not route.called


def test_market_hours(api, backend):
    route = api.get("/marketdata/v1/markets").respond(json=load("market_hours.json"))
    out = payload(call("get_market_hours", markets=["equity", "option"], date="2025-01-02"))
    sent = route.calls.last.request.url.params
    assert sent["markets"] == "equity,option" and sent["date"] == "2025-01-02"
    eq, opt = out["markets"]
    assert eq["is_trading_day"] is True
    assert eq["sessions"]["regularMarket"] == [["2025-01-02T09:30:00-05:00", "2025-01-02T16:00:00-05:00"]]
    assert opt["is_trading_day"] is False and opt["regular_session_open_now"] is False


def test_market_hours_open_now():
    import datetime as dt

    from schwab_mcp.formatting import shape_market_hours

    from .conftest import load as _load

    now = dt.datetime(2025, 1, 2, 15, 0, tzinfo=dt.timezone.utc)  # 10:00 New York
    out = shape_market_hours(_load("market_hours.json"), now=now)
    assert out["markets"][0]["regular_session_open_now"] is True


def test_market_hours_rejects_unknown_market(api, backend):
    assert "markets" in error_text(call("get_market_hours", markets=["crypto"]))


def test_tools_are_marked_read_only():
    import asyncio

    from schwab_mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    assert {t.name for t in tools} == {
        "get_accounts", "get_account_summary", "get_positions", "get_quote",
        "get_price_history", "get_option_chain", "get_market_hours",
    }
    for t in tools:
        assert t.annotations.read_only_hint is True
        assert t.annotations.destructive_hint is False


def test_unexpected_exception_is_reported_not_raised(api, backend):
    api.get("/marketdata/v1/quotes").mock(side_effect=lambda req: httpx.Response(200, content=b"not json"))
    msg = error_text(call("get_quote", symbols=["AAPL"]))
    assert "Unexpected error" in msg
