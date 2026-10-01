"""Technical indicators on daily OHLC data (pure pandas/numpy, no TA library needed).

RSI and ATR use Wilder's smoothing seeded with a simple average of the first
`period` values, which matches the definitions most charting platforms use.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """Wilder's moving average: seed with the SMA of the first `period` valid values,
    then avg_t = (avg_{t-1} * (period - 1) + x_t) / period."""
    values = series.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    valid = np.flatnonzero(~np.isnan(values))
    if len(valid) < period:
        return pd.Series(out, index=series.index)
    start = valid[0]
    seed_end = start + period
    if np.isnan(values[start:seed_end]).any():
        # Gaps inside the seed window: fall back to an exponential approximation.
        return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    out[seed_end - 1] = values[start:seed_end].mean()
    for i in range(seed_end, len(values)):
        x = values[i]
        out[i] = out[i - 1] if np.isnan(x) else (out[i - 1] * (period - 1) + x) / period
    return pd.Series(out, index=series.index)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    avg_gain = wilder_smooth(delta.clip(lower=0), period)
    avg_loss = wilder_smooth(-delta.clip(upper=0), period)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        values = 100 - 100 / (1 + rs)
    # No losses in the window -> 100; no movement at all -> neutral 50.
    values = values.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    values = values.mask((avg_loss == 0) & (avg_gain == 0), 50.0)
    return values


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    signal_line = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": signal_line, "macd_hist": line - signal_line})


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    ranges = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1)
    return ranges.max(axis=1, skipna=True)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = true_range(high, low, close)
    tr.iloc[0] = np.nan  # first bar has no previous close; skip it as Wilder does
    return wilder_smooth(tr, period)


def crossed_above(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


def crossed_below(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))


def compute_all(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """Return a copy of `df` (columns open/high/low/close/volume) with indicator columns added."""
    out = df.copy()
    close = out["close"]
    out["sma_fast"] = sma(close, cfg.sma_fast)
    out["sma_slow"] = sma(close, cfg.sma_slow)
    out["rsi"] = rsi(close, cfg.rsi_period)
    out = out.join(macd(close, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal))
    out["atr"] = atr(out["high"], out["low"], close, cfg.atr_period)
    out["high_lookback"] = out["high"].rolling(cfg.trail_lookback, min_periods=1).max()
    out["chandelier_stop"] = out["high_lookback"] - cfg.atr_trail_mult * out["atr"]
    return out
