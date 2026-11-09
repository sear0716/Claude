"""Black-76 pricing on the forward, and implied volatility by bisection.

Working on the forward (taken from put-call parity in the chain itself) means dividends and
stock-borrow costs are already in the price, so no dividend yield has to be guessed.
"""

from __future__ import annotations

import math

SQRT2 = math.sqrt(2.0)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1(forward: float, strike: float, vol: float, t: float) -> float:
    return (math.log(forward / strike) + 0.5 * vol * vol * t) / (vol * math.sqrt(t))


def black76(forward: float, strike: float, vol: float, t: float, df: float, right: str) -> float:
    """Price of a European option on a forward. df is the discount factor to expiry."""
    if t <= 0 or vol <= 0:
        intrinsic = forward - strike if right == "C" else strike - forward
        return df * max(intrinsic, 0.0)
    d1 = _d1(forward, strike, vol, t)
    d2 = d1 - vol * math.sqrt(t)
    if right == "C":
        return df * (forward * norm_cdf(d1) - strike * norm_cdf(d2))
    return df * (strike * norm_cdf(-d2) - forward * norm_cdf(-d1))


def delta(forward: float, strike: float, vol: float, t: float, right: str) -> float:
    """Forward delta (undiscounted): N(d1) for calls, N(d1)-1 for puts."""
    d1 = _d1(forward, strike, vol, t)
    return norm_cdf(d1) if right == "C" else norm_cdf(d1) - 1.0


def vega(forward: float, strike: float, vol: float, t: float, df: float) -> float:
    """Price change per 1.00 (100 vol points) change in volatility."""
    return df * forward * norm_pdf(_d1(forward, strike, vol, t)) * math.sqrt(t)


def implied_vol(price: float, forward: float, strike: float, t: float, df: float, right: str,
                lo: float = 1e-4, hi: float = 6.0) -> float | None:
    """Volatility that reproduces price, or None when the price is outside no-arbitrage bounds."""
    if t <= 0 or price <= 0 or forward <= 0 or strike <= 0:
        return None
    if price <= black76(forward, strike, lo, t, df, right) or price >= black76(forward, strike, hi, t, df, right):
        return None
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if black76(forward, strike, mid, t, df, right) > price:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-7:
            break
    return 0.5 * (lo + hi)
