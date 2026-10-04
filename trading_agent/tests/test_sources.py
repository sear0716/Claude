import json
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from trading_agent.config import Config
from trading_agent.sources.fred import FredSource, evaluate_regime, parse_api_observations, parse_csv
from trading_agent.sources.gdelt import GdeltSource, company_query, parse_articles, parse_tone
from trading_agent.sources.ibkr import IBKRSource, ib_symbol
from trading_agent.sources.offline import CSVSource, DemoSource
from trading_agent.sources.sec_edgar import (
    SecEdgarSource,
    parse_company_facts,
    parse_recent_filings,
    sec_ticker,
)


class FakeHttp:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get_text(self, url, params=None, ttl=0, headers=None):
        self.calls.append((url, params))
        for key, body in self.routes.items():
            if key in url + json.dumps(params or {}):
                return body if isinstance(body, str) else json.dumps(body)
        raise AssertionError(f"unexpected url {url} {params}")

    def get_json(self, url, params=None, ttl=0, headers=None):
        return json.loads(self.get_text(url, params, ttl, headers))


# --- SEC EDGAR ---------------------------------------------------------------------------

FACTS = {
    "cik": 320193,
    "entityName": "Apple Inc.",
    "facts": {
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "units": {"shares": [
                    {"end": "2025-10-17", "val": 14_800_000_000, "filed": "2025-10-31", "form": "10-K"},
                    {"end": "2026-07-18", "val": 14_700_000_000, "filed": "2026-08-01", "form": "10-Q"},
                ]}
            }
        },
        "us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {"USD": [
                    {"start": "2023-10-01", "end": "2024-09-28", "val": 391e9, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01"},
                    {"start": "2024-09-29", "end": "2025-09-27", "val": 416e9, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31"},
                    # quarterly value inside a 10-K must be ignored
                    {"start": "2025-06-29", "end": "2025-09-27", "val": 102e9, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31"},
                    {"start": "2025-12-28", "end": "2026-03-28", "val": 95e9, "fy": 2026, "fp": "Q2", "form": "10-Q", "filed": "2026-05-01"},
                ]}
            },
            "NetIncomeLoss": {
                "units": {"USD": [
                    {"start": "2024-09-29", "end": "2025-09-27", "val": 112e9, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31"},
                ]}
            },
            "EarningsPerShareDiluted": {
                "units": {"USD/shares": [
                    {"start": "2024-09-29", "end": "2025-09-27", "val": 7.46, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31"},
                ]}
            },
        },
    },
}

SUBMISSIONS = {
    "cik": "320193",
    "filings": {"recent": {
        "form": ["8-K", "4", "10-Q", "8-K"],
        "filingDate": ["2026-09-20", "2026-09-21", "2026-08-01", "2026-01-05"],
        "accessionNumber": ["0000320193-26-000100", "0000320193-26-000101", "0000320193-26-000090", "0000320193-26-000001"],
        "primaryDocument": ["a8k.htm", "form4.xml", "q.htm", "old.htm"],
    }},
}


def test_sec_ticker_share_class():
    assert sec_ticker("BRK.B") == "BRK-B"
    assert ib_symbol("BRK.B") == "BRK B"


def test_parse_company_facts():
    f = parse_company_facts("AAPL", FACTS)
    assert f.revenue == 416e9
    assert f.revenue_growth == pytest.approx(416 / 391 - 1)
    assert f.net_income == 112e9 and f.profitable
    assert f.eps_diluted == 7.46
    assert f.shares_outstanding == 14_700_000_000
    assert f.fiscal_year_end == "2025-09-27"


def test_parse_recent_filings_filters_window_and_insider_forms():
    out = parse_recent_filings(SUBMISSIONS, days=30, today=date(2026, 10, 1))
    assert [x["form"] for x in out] == ["8-K"]
    assert out[0]["url"].endswith("/320193/000032019326000100/a8k.htm")


def test_sec_source_end_to_end():
    http = FakeHttp({
        "company_tickers": {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}},
        "companyfacts/CIK0000320193": FACTS,
        "submissions/CIK0000320193": SUBMISSIONS,
    })
    src = SecEdgarSource(http)
    f = src.fundamentals("AAPL")
    assert f.cik == 320193 and f.revenue == 416e9
    assert src.fundamentals("NOPE").revenue is None


# --- FRED --------------------------------------------------------------------------------

def test_fred_parsers():
    s = parse_api_observations({"observations": [
        {"date": "2026-09-01", "value": "4.10"}, {"date": "2026-09-02", "value": "."},
    ]})
    assert list(s.values) == [4.10]
    c = parse_csv("observation_date,DGS10\n2026-09-01,4.1\n2026-09-02,\n2026-09-03,4.2\n")
    assert list(c.values) == [4.1, 4.2]


def _monthly(values):
    return pd.Series(values, index=pd.date_range("2025-01-01", periods=len(values), freq="MS"))


def test_regime_risk_off():
    snap = evaluate_regime({
        "T10Y2Y": pd.Series([-0.4]),
        "CPIAUCSL": _monthly(np.linspace(300, 313, 13)),  # ~4.3% y/y
        "UNRATE": _monthly([4.0] * 10 + [4.6, 4.7, 4.8]),
        "BAMLH0A0HYM2": pd.Series([5.6]),
        "VIXCLS": pd.Series([31.0]),
    })
    assert snap.regime == "risk_off" and snap.score == -5
    assert snap.size_multiplier == 0.5


def test_regime_risk_on():
    snap = evaluate_regime({
        "T10Y2Y": pd.Series([0.8]),
        "CPIAUCSL": _monthly(np.linspace(300, 306, 13)),  # 2% y/y
        "BAMLH0A0HYM2": pd.Series([3.0]),
        "VIXCLS": pd.Series([13.0]),
    })
    assert snap.regime == "risk_on" and snap.score == 4


def test_fred_source_uses_csv_without_key_and_api_with_key():
    csv_http = FakeHttp({"fredgraph.csv": "observation_date,X\n2026-09-01,1.5\n"})
    assert FredSource(csv_http).series("X").iloc[-1] == 1.5
    api_http = FakeHttp({"series/observations": {"observations": [{"date": "2026-09-01", "value": "2.5"}]}})
    assert FredSource(api_http, api_key="k").series("X").iloc[-1] == 2.5
    assert api_http.calls[0][1]["api_key"] == "k"


# --- GDELT -------------------------------------------------------------------------------

def test_company_query_strips_suffixes():
    assert company_query("Apple Inc.") == '"Apple" sourcelang:english'
    assert company_query("Exxon Mobil Corp") == '"Exxon Mobil" sourcelang:english'
    assert company_query("Berkshire Hathaway Inc /DE/").startswith('"Berkshire Hathaway')


def test_gdelt_parsing_and_source():
    tone = {"timeline": [{"series": "Average Tone", "data": [{"date": "x", "value": -1.0}, {"date": "y", "value": -3.0}]}]}
    arts = {"articles": [{"title": f"t{i}", "url": f"u{i}", "domain": "d", "seendate": "s"} for i in range(8)]}
    assert parse_tone(tone) == -2.0
    assert len(parse_articles(arts, 3)) == 3
    http = FakeHttp({'"mode": "timelinetone"': tone, '"mode": "artlist"': arts})
    sig = GdeltSource(http).company_news("Apple Inc.")
    assert sig.avg_tone == -2.0 and sig.article_count == 8 and len(sig.headlines) == 5


def test_gdelt_plaintext_error_raises():
    http = FakeHttp({"gdelt": "Your search contained a phrase that was too short."})
    with pytest.raises(ValueError):
        GdeltSource(http).news("x")


# --- IBKR (fake IB client, no TWS needed) ------------------------------------------------

class FakeIB:
    def __init__(self):
        self.connected = True

    def isConnected(self):
        return self.connected

    def disconnect(self):
        self.connected = False

    def accountSummary(self, account=""):
        return [
            SimpleNamespace(tag="NetLiquidation", value="250000.5", currency="USD"),
            SimpleNamespace(tag="BuyingPower", value="500000", currency="USD"),
            SimpleNamespace(tag="AccountType", value="INDIVIDUAL", currency=""),
        ]

    def positions(self, account=""):
        return [
            SimpleNamespace(account="U1", contract=SimpleNamespace(secType="STK", symbol="BRK B"), position=10, avgCost=400.0),
            SimpleNamespace(account="U1", contract=SimpleNamespace(secType="OPT", symbol="AAPL"), position=1, avgCost=5.0),
        ]

    def qualifyContracts(self, contract):
        self.last_contract = contract
        return [contract]

    def reqHistoricalData(self, contract, **kw):
        from ib_async import BarData

        self.hist_kwargs = kw
        days = pd.bdate_range(end="2026-09-30", periods=300)
        return [BarData(date=d.date(), open=1.0, high=2.0, low=0.5, close=1.5, volume=100) for d in days]


def test_ibkr_source_with_fake_client():
    cfg = Config()
    cfg.ib_request_pause = 0
    ib = FakeIB()
    src = IBKRSource(cfg, ib=ib)
    assert src.account_summary() == {"NetLiquidation": 250000.5, "BuyingPower": 500000.0}
    holdings = src.positions()
    assert [(h.symbol, h.quantity) for h in holdings] == [("BRK.B", 10.0)]
    df = src.history("BRK.B", 250)
    assert ib.last_contract.symbol == "BRK B"
    assert ib.hist_kwargs["whatToShow"] == "ADJUSTED_LAST"
    assert len(df) == 250 and list(df.columns) == ["open", "high", "low", "close", "volume"]
    src.close()
    assert not ib.connected


# --- offline -----------------------------------------------------------------------------

def test_csv_and_demo_sources(tmp_path):
    pd.DataFrame({
        "Date": pd.bdate_range("2026-01-01", periods=5), "Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5,
    }).to_csv(tmp_path / "BRK-B.csv", index=False)
    df = CSVSource(tmp_path).history("BRK.B", 3)
    assert len(df) == 3 and (df.volume == 0).all()
    a, b = DemoSource().history("AAPL", 50), DemoSource().history("AAPL", 50)
    pd.testing.assert_frame_equal(a, b)
    assert (a.high >= a.low).all()
