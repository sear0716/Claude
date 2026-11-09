"""Realised-volatility estimators and a horizon-matched volatility forecast."""

from __future__ import annotations

import math

from .models import Bar

TRADING_DAYS = 252


def log_returns(closes: list[float]) -> list[float]:
    return [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]


def close_to_close(closes: list[float], window: int) -> float | None:
    """Annualised standard deviation of the last `window` daily log returns."""
    r = log_returns(closes)[-window:]
    if len(r) < max(5, window // 2):
        return None
    mean = sum(r) / len(r)
    var = sum((x - mean) ** 2 for x in r) / (len(r) - 1)
    return math.sqrt(var * TRADING_DAYS)


def yang_zhang(bars: list[Bar], window: int) -> float | None:
    """Yang-Zhang (2000) estimator: uses open/high/low/close, handles overnight gaps and drift."""
    b = bars[-(window + 1):]
    n = len(b) - 1
    if n < max(5, window // 2):
        return None
    overnight, open_close, rs = [], [], []
    for prev, cur in zip(b, b[1:]):
        if min(prev.close, cur.open, cur.high, cur.low, cur.close) <= 0:
            return None
        overnight.append(math.log(cur.open / prev.close))
        open_close.append(math.log(cur.close / cur.open))
        u, d, c = (math.log(cur.high / cur.open), math.log(cur.low / cur.open), math.log(cur.close / cur.open))
        rs.append(u * (u - c) + d * (d - c))  # Rogers-Satchell term

    def sample_var(xs: list[float]) -> float:
        m = sum(xs) / len(xs)
        return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)

    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    var = sample_var(overnight) + k * sample_var(open_close) + (1 - k) * sum(rs) / n
    return math.sqrt(max(var, 0.0) * TRADING_DAYS)


def ewma(closes: list[float], lam: float = 0.94) -> float | None:
    """RiskMetrics exponentially weighted volatility: today's estimate of the current vol level."""
    r = log_returns(closes)
    if len(r) < 20:
        return None
    var = sum(x * x for x in r[:20]) / 20
    for x in r[20:]:
        var = lam * var + (1 - lam) * x * x
    return math.sqrt(var * TRADING_DAYS)


def forecast(closes: list[float], horizon_days: float, half_life: float = 30.0) -> float | None:
    """Expected average volatility over the next `horizon_days` trading days.

    Volatility clusters and mean-reverts, so the forecast starts at the current level (EWMA) and
    decays towards the long-run level (up to 1 year of history) with the given half-life, the same
    shape a GARCH(1,1) term structure has. Short horizons stay near current vol; long ones near the mean.
    """
    current = ewma(closes)
    long_run = close_to_close(closes, min(TRADING_DAYS, len(closes) - 1))
    if current is None or long_run is None:
        return None
    x = max(horizon_days, 1.0) * math.log(2.0) / half_life
    weight = (1.0 - math.exp(-x)) / x  # average weight on the current level across the horizon
    return math.sqrt(weight * current**2 + (1.0 - weight) * long_run**2)


def rolling_close_to_close(closes: list[float], window: int) -> list[float]:
    """Every `window`-day realised vol in the history (used as a stand-in for an IV history)."""
    r = log_returns(closes)
    out = []
    for i in range(window, len(r) + 1):
        seg = r[i - window:i]
        mean = sum(seg) / window
        out.append(math.sqrt(sum((x - mean) ** 2 for x in seg) / (window - 1) * TRADING_DAYS))
    return out


def percentile_of(value: float, history: list[float]) -> float | None:
    """Share of history at or below value, 0..1."""
    return sum(1 for h in history if h <= value) / len(history) if history else None


def horizon_gross_returns(closes: list[float], horizon_days: int) -> list[float]:
    """Gross returns S[t+h]/S[t] over every overlapping h-day window."""
    return [closes[i + horizon_days] / closes[i] for i in range(len(closes) - horizon_days) if closes[i] > 0]
