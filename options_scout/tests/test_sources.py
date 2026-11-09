import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("ib_async")

from options_scout.sources.ibkr import IBKROptionsSource, options_config  # noqa: E402
from options_scout.sources.yahoo import YahooSource  # noqa: E402

TODAY = pd.Timestamp.today().normalize()


class FakeIB:
    def __init__(self):
        self.cancelled = []
        self.mkt_type = None
        self.qualify_calls = 0

    def isConnected(self):
        return True

    def qualifyContracts(self, *contracts):
        self.qualify_calls += 1
        out = []
        for i, c in enumerate(contracts):
            if c.secType == "OPT" and c.strike == 105:  # 105 is not listed: IB returns None
                out.append(None)
                continue
            c.conId = 1000 + i
            out.append(c)
        return out

    def reqMarketDataType(self, t):
        self.mkt_type = t

    def reqTickers(self, *contracts):
        return [SimpleNamespace(marketPrice=lambda: 101.0, close=100.0)]

    def reqSecDefOptParams(self, sym, fut, sec_type, con_id):
        exp = [(TODAY + pd.Timedelta(days=d)).strftime("%Y%m%d") for d in (3, 30, 60, 400)]
        return [
            SimpleNamespace(exchange="SMART", tradingClass="2XYZ", multiplier="100", expirations=exp[:1], strikes=[100]),
            SimpleNamespace(exchange="SMART", tradingClass="XYZ", multiplier="100", expirations=exp,
                            strikes=[50, 95, 100, 105, 200]),
        ]

    def reqMktData(self, contract, ticks, snapshot, regulatory):
        assert ticks == "101" and not snapshot
        oi = 700.0 if contract.right == "C" else math.nan
        return SimpleNamespace(bid=1.0, ask=1.2, last=1.1, volume=5.0, callOpenInterest=oi,
                               putOpenInterest=math.nan, openInterest=300.0,
                               modelGreeks=SimpleNamespace(impliedVol=0.25))

    def sleep(self, s):
        pass

    def cancelMktData(self, c):
        self.cancelled.append(c)

    def reqHistoricalData(self, contract, **kw):
        assert kw["whatToShow"] == "OPTION_IMPLIED_VOLATILITY"
        return [SimpleNamespace(date=TODAY - pd.Timedelta(days=i), close=0.2 + i / 1000) for i in range(30)]


def test_ibkr_chain_filters_and_quotes(monkeypatch):
    monkeypatch.setenv("OPTIONS_IB_CLIENT_ID", "23")
    assert options_config().ib_client_id == 23
    ib = FakeIB()
    src = IBKROptionsSource(ib=ib, batch_size=3, quote_wait=0)
    chain = src.chain("XYZ", 7, 120, 0.75, 1.25)
    # expiries 30 and 60 only; strikes 95/100 (105 unlisted, 50/200 outside band); C and P
    assert sorted(chain["strike"].unique()) == [95, 100]
    assert len(chain) == 2 * 2 * 2
    assert set((chain["expiry"] - TODAY).dt.days) == {30, 60}
    calls, puts = chain[chain.right == "C"], chain[chain.right == "P"]
    assert (calls["open_interest"] == 700).all() and (puts["open_interest"] == 300).all()
    assert (chain["source_iv"] == 0.25).all()
    assert len(ib.cancelled) == len(chain)
    assert ib.mkt_type == 1


def test_ibkr_iv_history():
    s = IBKROptionsSource(ib=FakeIB()).iv_history("XYZ")
    assert len(s) == 30 and s.max() == pytest.approx(0.229)


class FakeTicker:
    def __init__(self):
        exp1 = (TODAY + pd.Timedelta(days=30)).strftime("%Y-%m-%d")
        exp2 = (TODAY + pd.Timedelta(days=300)).strftime("%Y-%m-%d")
        self.options = (exp1, exp2)
        self.fast_info = {"last_price": 100.0}
        idx = pd.DatetimeIndex([TODAY - pd.Timedelta(days=100), TODAY - pd.Timedelta(days=10)], tz="America/New_York")
        self.dividends = pd.Series([0.5, 0.5], index=idx)

    def option_chain(self, exp):
        frame = pd.DataFrame({"strike": [60.0, 100.0, 110.0], "lastPrice": [40, 3, 1], "bid": [39, 2.9, 0.9],
                              "ask": [41, 3.1, 1.1], "volume": [1, 2, 3], "openInterest": [10, 20, 30],
                              "impliedVolatility": [0.5, 0.3, 0.28]})
        return SimpleNamespace(calls=frame, puts=frame.copy())

    def history(self, **kw):
        idx = pd.date_range(end=TODAY, periods=300, tz="America/New_York")
        c = np.linspace(90, 100, 300)
        return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1.0}, index=idx)


def test_yahoo_source_with_fake_module():
    src = YahooSource(yf_module=SimpleNamespace(Ticker=lambda s: FakeTicker()))
    chain = src.chain("XYZ", 7, 120, 0.75, 1.25)
    assert len(chain) == 4  # one expiry in range; 60 strike outside band; calls + puts
    assert set(chain["right"]) == {"C", "P"}
    hist = src.history("XYZ", 250)
    assert len(hist) == 250 and hist.index.tz is None
    assert src.dividend_yield("XYZ") == pytest.approx(0.01)
