"""Realized-volatility estimators, a horizon-matched volatility forecast and IV rank.

Every estimator returns an annualized decimal volatility (0.25 = 25%).
`ohlc` is a DataFrame with open/high/low/close columns indexed by date.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def log_returns(close: pd.Series) -> pd.Series:
    return np.log(close / close.shift(1)).dropna()


def close_to_close(ohlc: pd.DataFrame, window: int) -> float:
    r = log_returns(ohlc["close"]).tail(window)
    if len(r) < 2:
        return float("nan")
    return float(r.std(ddof=1) * math.sqrt(TRADING_DAYS))


def parkinson(ohlc: pd.DataFrame, window: int) -> float:
    """High-low range estimator (Parkinson 1980). ~5x more efficient than close-to-close, ignores gaps."""
    x = ohlc.tail(window)
    hl = np.log(x["high"] / x["low"]) ** 2
    return float(math.sqrt(hl.mean() / (4 * math.log(2)) * TRADING_DAYS))


def yang_zhang(ohlc: pd.DataFrame, window: int) -> float:
    """Yang-Zhang (2000): overnight gaps + open-to-close + Rogers-Satchell intraday range.

    Unbiased under drift and handles opening jumps, which matter for equities.
    """
    x = ohlc.tail(window + 1)
    o, h, l, c = (np.log(x[k].astype(float)) for k in ("open", "high", "low", "close"))
    overnight = (o - c.shift(1)).dropna()
    open_close = (c - o).iloc[1:]
    rs = ((h - c) * (h - o) + (l - c) * (l - o)).iloc[1:]
    n = len(open_close)
    if n < 2:
        return float("nan")
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    var = overnight.var(ddof=1) + k * open_close.var(ddof=1) + (1 - k) * rs.mean()
    return float(math.sqrt(max(var, 0.0) * TRADING_DAYS))


def ewma(ohlc: pd.DataFrame, lam: float = 0.94) -> float:
    """RiskMetrics exponentially weighted volatility: the 'current' vol level."""
    r = log_returns(ohlc["close"])
    if len(r) < 2:
        return float("nan")
    weights = (1 - lam) * lam ** np.arange(len(r))[::-1]
    var = float(np.sum(weights * r.to_numpy() ** 2) / weights.sum())
    return math.sqrt(var * TRADING_DAYS)


@dataclass
class VolProfile:
    """What the stock has actually been doing, plus the inputs to the forecast."""

    rv10: float
    rv20: float
    rv60: float
    rv252: float
    yz20: float
    parkinson20: float
    ewma: float
    current: float  # blended near-term level
    long_run: float  # 1-year level the forecast mean-reverts to
    half_life_days: float

    def forecast(self, trading_days: float) -> float:
        """Expected average volatility over the next `trading_days`.

        Variance mean-reverts from `current` to `long_run` with the given half-life
        (the term structure of a GARCH(1,1)):
            avg var(T) = long + (current - long) * (1 - e^{-kT}) / (kT),  k = ln2 / half-life
        Short options get roughly today's vol, long-dated ones roughly the 1-year average.
        """
        cur, lng = self.current ** 2, self.long_run ** 2
        kt = math.log(2) / self.half_life_days * max(trading_days, 1e-6)
        weight = (1 - math.exp(-kt)) / kt
        return math.sqrt(lng + (cur - lng) * weight)


def vol_profile(ohlc: pd.DataFrame, half_life_days: float = 30.0) -> VolProfile:
    rv10, rv20, rv60 = (close_to_close(ohlc, w) for w in (10, 20, 60))
    rv252 = close_to_close(ohlc, TRADING_DAYS)
    yz20 = yang_zhang(ohlc, 20)
    pk20 = parkinson(ohlc, 20)
    ew = ewma(ohlc)
    near = [v for v in (yz20, ew, rv20) if np.isfinite(v)]
    current = float(np.mean(near)) if near else float("nan")
    long_run = rv252 if np.isfinite(rv252) else rv60
    return VolProfile(rv10, rv20, rv60, rv252, yz20, pk20, ew, current, long_run, half_life_days)


def iv_rank(series: pd.Series, current: float) -> tuple[float | None, float | None]:
    """IV rank and IV percentile (0-100) of `current` against a history of IV readings.

    rank       = (current - min) / (max - min)
    percentile = share of days the IV was below current
    Needs at least 20 observations, else (None, None).
    """
    s = pd.Series(series, dtype=float).dropna()
    if len(s) < 20 or not np.isfinite(current):
        return None, None
    lo, hi = float(s.min()), float(s.max())
    rank = 100.0 * (current - lo) / (hi - lo) if hi > lo else 50.0
    pct = 100.0 * float((s < current).mean())
    return float(np.clip(rank, 0, 100)), pct
