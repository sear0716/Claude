import numpy as np
import pandas as pd
import pytest

from options_scout.ivstore import IVStore
from options_scout.pricing import bsm_price
from options_scout.report import to_markdown, write_reports
from options_scout.scanner import ScanParams, Scanner, atm_iv_30, top_cheap
from options_scout.sources import DemoSource
from options_scout.sources.base import normalize_chain

TODAY = pd.Timestamp("2026-11-09")


class FlatSource:
    """Stock that realizes `rv`; options priced on a flat `iv` except one strike at `cheap_iv`."""

    def __init__(self, rv, iv, cheap_strike=None, cheap_iv=None, spread=0.02):
        self.rv, self.iv, self.cheap_strike, self.cheap_iv, self.spread = rv, iv, cheap_strike, cheap_iv, spread

    def history(self, symbol, days):
        rng = np.random.default_rng(7)
        r = rng.normal(0, self.rv / np.sqrt(252), 400)
        r = (r - r.mean()) / r.std() * self.rv / np.sqrt(252)  # realize exactly rv
        close = 100 * np.exp(np.cumsum(r))
        close *= 100 / close[-1]
        idx = pd.bdate_range(end=TODAY, periods=400)
        return pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0}, index=idx)

    def spot(self, symbol):
        return 100.0

    def chain(self, symbol, min_dte, max_dte, lo_k, hi_k):
        rows = []
        for dte in (30, 60):
            for k in np.arange(80, 121, 5.0):
                for right in "CP":
                    sig = self.cheap_iv if (k == self.cheap_strike and dte == 30) else self.iv
                    p = float(bsm_price(100.0, k, dte / 365, 0.04, 0.0, sig, right))
                    h = max(0.01, p * self.spread / 2)
                    rows.append({"expiry": TODAY + pd.Timedelta(days=dte), "strike": k, "right": right,
                                 "bid": p - h, "ask": p + h, "last": p, "volume": 10, "open_interest": 500})
        return normalize_chain(pd.DataFrame(rows))

    def iv_history(self, symbol):
        return pd.Series(np.linspace(0.2, 0.6, 252))

    def dividend_yield(self, symbol):
        return 0.0

    def close(self):
        pass


def params(**kw):
    return ScanParams(rate=0.04, min_open_interest=100, **kw)


def test_rich_market_has_no_cheap_options():
    summary, contracts = Scanner(FlatSource(rv=0.20, iv=0.30), params(), today=TODAY).scan_symbol("X")
    assert summary.atm_iv30 == pytest.approx(0.30, abs=0.005)
    assert summary.iv_rv_ratio30 > 1.3
    assert summary.contracts_cheap == 0
    assert top_cheap(contracts).empty


def test_cheap_market_flags_all_liquid_options():
    summary, contracts = Scanner(FlatSource(rv=0.30, iv=0.20), params(), today=TODAY).scan_symbol("X")
    assert summary.contracts_cheap == summary.contracts_passing > 0
    top = top_cheap(contracts)
    assert (top["vol_gap"] > 0.25).all()
    assert (top["edge_pct"] > 0).all()
    # IV history spans 20-60%; 20% is the bottom of the range
    assert summary.iv_rank == pytest.approx(0.0, abs=1)


def test_single_mispriced_strike_ranks_first_by_smile():
    src = FlatSource(rv=0.25, iv=0.25, cheap_strike=105.0, cheap_iv=0.21)
    _, contracts = Scanner(src, params(), today=TODAY).scan_symbol("X")
    best = contracts.sort_values("score", ascending=False).iloc[0]
    assert best["strike"] == 105.0 and best["days"] == 30
    assert best["smile_resid"] < -0.02
    assert best["cheap"]


def test_wide_spreads_fail_the_filter():
    _, contracts = Scanner(FlatSource(rv=0.3, iv=0.2, spread=0.5), params(), today=TODAY).scan_symbol("X")
    assert not contracts["passes"].any()


def test_atm_iv_30_interpolates_total_variance():
    atm = pd.DataFrame({"days": [10.0, 50.0], "atm_iv": [0.40, 0.20]})
    expected = np.sqrt((0.4**2 * 10 + (0.2**2 * 50 - 0.4**2 * 10) * 0.5) / 30)
    assert atm_iv_30(atm) == pytest.approx(expected)


def test_iv_store_round_trip(tmp_path):
    store = IVStore(tmp_path)
    store.record("abc", TODAY, 0.3)
    store.record("ABC", TODAY, 0.31)  # same day overwrites
    store.record("ABC", TODAY + pd.Timedelta(days=1), 0.32)
    s = store.load("ABC")
    assert list(s.round(2)) == [0.31, 0.32]


def test_demo_end_to_end_and_reports(tmp_path):
    scanner = Scanner(DemoSource(today=TODAY), params(), IVStore(tmp_path / "iv"), today=TODAY)
    summaries, contracts = scanner.scan(["AAPL", "MSFT"])
    assert len(summaries) == 2 and not contracts.empty
    top = top_cheap(contracts, 10)
    assert not top.empty and top["cheap"].all()
    meta = {"source": "demo", "rate": 0.04, "min_dte": 7, "max_dte": 120, "generated": "now"}
    md = to_markdown(summaries, top, meta)
    assert "Cheapest contracts" in md and "AAPL" in md
    paths = write_reports(tmp_path / "out", summaries, contracts, top, meta)
    assert all(p.exists() for p in paths.values())


def test_bad_symbol_is_skipped():
    class Broken(FlatSource):
        def history(self, symbol, days):
            if symbol == "BAD":
                raise LookupError("nope")
            return super().history(symbol, days)

    summaries, _ = Scanner(Broken(0.2, 0.2), params(), today=TODAY).scan(["BAD", "OK"])
    assert [s.symbol for s in summaries] == ["OK"]
