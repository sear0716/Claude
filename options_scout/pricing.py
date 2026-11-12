"""Black-Scholes-Merton pricing, greeks and implied volatility (vectorized numpy).

All rates are continuously compounded annual decimals; time is in years.
Listed US equity options are American. For calls on stocks without large
dividends early exercise is never optimal, so BSM is exact. For puts and
deep in-the-money options BSM slightly understates the price; the scanner
mostly looks at out-of-the-money and near-the-money contracts where the gap is small.
"""

from __future__ import annotations

import math

import numpy as np

_SQRT2 = math.sqrt(2.0)
_erf = np.frompyfunc(math.erf, 1, 1)


def norm_cdf(x):
    x = np.asarray(x, dtype=float)
    return 0.5 * (1.0 + np.asarray(_erf(x / _SQRT2), dtype=float))


def norm_pdf(x):
    x = np.asarray(x, dtype=float)
    return np.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(S, K, T, r, q, sigma):
    S, K, T, sigma = (np.asarray(v, dtype=float) for v in (S, K, T, sigma))
    with np.errstate(divide="ignore", invalid="ignore"):
        vol_t = sigma * np.sqrt(T)
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vol_t
    return d1, d1 - vol_t


def _is_call(right) -> np.ndarray:
    return np.char.upper(np.asarray(right, dtype=str)) == "C"


def bsm_price(S, K, T, r, q, sigma, right):
    """Price of a European call ('C') or put ('P')."""
    S, K, T, sigma = (np.asarray(v, dtype=float) for v in (S, K, T, sigma))
    d1, d2 = _d1_d2(S, K, T, r, q, sigma)
    disc_q, disc_r = np.exp(-q * T), np.exp(-r * T)
    call = S * disc_q * norm_cdf(d1) - K * disc_r * norm_cdf(d2)
    put = K * disc_r * norm_cdf(-d2) - S * disc_q * norm_cdf(-d1)
    price = np.where(_is_call(right), call, put)
    # sigma -> 0 or T -> 0: discounted intrinsic value
    intrinsic = np.where(
        _is_call(right), np.maximum(S * disc_q - K * disc_r, 0.0), np.maximum(K * disc_r - S * disc_q, 0.0)
    )
    return np.where(np.isfinite(price), price, intrinsic)


def bsm_delta(S, K, T, r, q, sigma, right):
    d1, _ = _d1_d2(S, K, T, r, q, sigma)
    disc_q = np.exp(-q * np.asarray(T, dtype=float))
    return np.where(_is_call(right), disc_q * norm_cdf(d1), disc_q * (norm_cdf(d1) - 1.0))


def bsm_vega(S, K, T, r, q, sigma):
    """dPrice/dSigma, per 1.00 (100 vol points) of volatility."""
    d1, _ = _d1_d2(S, K, T, r, q, sigma)
    T = np.asarray(T, dtype=float)
    return np.asarray(S, dtype=float) * np.exp(-q * T) * norm_pdf(d1) * np.sqrt(T)


def implied_vol(price, S, K, T, r, q, right, lo: float = 1e-4, hi: float = 5.0, iterations: int = 80):
    """Volatility that makes the BSM price equal `price` (vectorized bisection).

    Returns NaN where the price is outside the no-arbitrage bounds (below
    discounted intrinsic value or above the stock/strike bound) or not positive.
    """
    price = np.asarray(price, dtype=float)
    shape = np.broadcast(price, np.asarray(S), np.asarray(K), np.asarray(T), np.asarray(right)).shape
    price, S, K, T = (np.broadcast_to(np.asarray(v, dtype=float), shape) for v in (price, S, K, T))
    right = np.broadcast_to(np.asarray(right, dtype=str), shape)
    p_lo = bsm_price(S, K, T, r, q, np.full(shape, lo), right)
    p_hi = bsm_price(S, K, T, r, q, np.full(shape, hi), right)
    valid = np.isfinite(price) & (price > 0) & (T > 0) & (price >= p_lo - 1e-9) & (price <= p_hi)
    a = np.full(shape, lo)
    b = np.full(shape, hi)
    for _ in range(iterations):
        mid = 0.5 * (a + b)
        too_high = bsm_price(S, K, T, r, q, mid, right) > price
        b = np.where(too_high, mid, b)
        a = np.where(too_high, a, mid)
    iv = 0.5 * (a + b)
    return np.where(valid, iv, np.nan)
