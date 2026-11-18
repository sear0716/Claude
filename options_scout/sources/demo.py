"""Deterministic synthetic stock + option chain, for trying the scanner offline.

NOT market data. The chain is priced off a smooth volatility smile that sits a
little above the stock's realized vol (like real markets), with a few contracts
deliberately mispriced cheap so you can see what the scanner flags.
"""

from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

from ..pricing import bsm_price
from .base import normalize_chain


class DemoSource:
    name = "demo"

    def __init__(self, today: pd.Timestamp | None = None, rate: float = 0.04):
        self.today = (today or pd.Timestamp.today()).normalize()
        self.rate = rate
        self._cache: dict[str, pd.DataFrame] = {}

    def _params(self, symbol: str):
        rng = np.random.default_rng(zlib.crc32(symbol.encode()))
        return rng, rng.uniform(0.18, 0.45)

    def history(self, symbol: str, days: int) -> pd.DataFrame:
        if symbol in self._cache:
            return self._cache[symbol].tail(days)
        rng, vol = self._params(symbol)
        n = max(days, 300)
        daily = vol / np.sqrt(252)
        rets = rng.normal(0.0003, daily, n)
        close = 100 * rng.uniform(0.5, 3) * np.exp(np.cumsum(rets))
        gap = rng.normal(0, daily * 0.4, n)
        open_ = np.r_[close[0], close[:-1]] * np.exp(gap)
        rng_hi = np.abs(rng.normal(0, daily * 0.6, n))
        rng_lo = np.abs(rng.normal(0, daily * 0.6, n))
        high = np.maximum(open_, close) * np.exp(rng_hi)
        low = np.minimum(open_, close) * np.exp(-rng_lo)
        idx = pd.bdate_range(end=self.today, periods=n)
        df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1e6}, index=idx)
        self._cache[symbol] = df
        return df.tail(days)

    def spot(self, symbol: str) -> float:
        return float(self.history(symbol, 300)["close"].iloc[-1])

    def chain(self, symbol: str, min_dte: int, max_dte: int, lo_k: float, hi_k: float) -> pd.DataFrame:
        rng, vol = self._params(symbol)
        S = self.spot(symbol)
        rows = []
        for dte in (7, 14, 30, 45, 60, 90, 120, 180):
            if not min_dte <= dte <= max_dte:
                continue
            T = dte / 365.0
            step = 2.5 if S < 150 else 5.0
            strikes = np.arange(np.floor(S * lo_k / step) * step, S * hi_k + step, step)
            m = np.log(strikes / S) / np.sqrt(T)
            # market smile: 3 vol pts over realized, put skew, a little curvature
            iv = vol + 0.03 - 0.10 * m + 0.25 * m * m
            iv = iv * (1 + rng.normal(0, 0.01, len(strikes)))
            for right in ("C", "P"):
                sig = iv.copy()
                cheap = rng.random(len(strikes)) < 0.06
                sig[cheap] -= 0.07  # planted cheap contracts
                fair = bsm_price(S, strikes, T, self.rate, 0.0, sig, np.full(len(strikes), right))
                half = np.maximum(0.01, fair * 0.02)
                for k, p, h in zip(strikes, fair, half):
                    if p < 0.05:
                        continue
                    rows.append(
                        {"expiry": self.today + pd.Timedelta(days=dte), "strike": k, "right": right,
                         "bid": round(p - h, 2), "ask": round(p + h, 2), "last": round(p, 2),
                         "volume": int(rng.integers(0, 3000)), "open_interest": int(rng.integers(50, 20000)),
                         "source_iv": np.nan}
                    )
        return normalize_chain(pd.DataFrame(rows))

    def iv_history(self, symbol: str) -> pd.Series:
        rng, vol = self._params(symbol)
        idx = pd.bdate_range(end=self.today - pd.Timedelta(days=1), periods=252)
        walk = np.cumsum(rng.normal(0, 0.01, len(idx)))
        return pd.Series(np.clip(vol + 0.03 + walk - walk.mean(), 0.05, None), index=idx)

    def dividend_yield(self, symbol: str) -> float:
        return 0.0

    def close(self) -> None:
        pass
