import math

import numpy as np
import pytest

from options_scout.pricing import bsm_delta, bsm_price, bsm_vega, implied_vol


def test_known_black_scholes_value():
    # Hull's textbook example: S=42, K=40, r=10%, sigma=20%, T=0.5 -> call 4.76, put 0.81
    assert bsm_price(42, 40, 0.5, 0.10, 0.0, 0.2, "C") == pytest.approx(4.76, abs=0.01)
    assert bsm_price(42, 40, 0.5, 0.10, 0.0, 0.2, "P") == pytest.approx(0.81, abs=0.01)


def test_put_call_parity_with_dividends():
    S, K, T, r, q, s = 100.0, 95.0, 0.75, 0.04, 0.02, 0.3
    c = bsm_price(S, K, T, r, q, s, "C")
    p = bsm_price(S, K, T, r, q, s, "P")
    assert c - p == pytest.approx(S * math.exp(-q * T) - K * math.exp(-r * T), abs=1e-9)


def test_implied_vol_round_trip_vectorized():
    K = np.array([80.0, 100.0, 120.0, 90.0])
    right = np.array(["P", "C", "C", "P"])
    sig = np.array([0.45, 0.25, 0.30, 0.6])
    prices = bsm_price(100.0, K, 0.25, 0.04, 0.01, sig, right)
    iv = implied_vol(prices, 100.0, K, 0.25, 0.04, 0.01, right)
    np.testing.assert_allclose(iv, sig, atol=1e-6)


def test_implied_vol_nan_below_intrinsic():
    # a 70-strike call on a 100 stock cannot trade at 5
    assert np.isnan(implied_vol(5.0, 100.0, 70.0, 0.5, 0.04, 0.0, "C"))
    assert np.isnan(implied_vol(0.0, 100.0, 100.0, 0.5, 0.04, 0.0, "C"))


def test_greeks():
    assert bsm_delta(100, 100, 1.0, 0.0, 0.0, 0.2, "C") == pytest.approx(0.5398, abs=1e-3)
    assert bsm_delta(100, 100, 1.0, 0.0, 0.0, 0.2, "P") == pytest.approx(0.5398 - 1, abs=1e-3)
    # vega matches a finite difference
    up = bsm_price(100, 105, 0.5, 0.03, 0.0, 0.2501, "C")
    dn = bsm_price(100, 105, 0.5, 0.03, 0.0, 0.2499, "C")
    assert bsm_vega(100, 105, 0.5, 0.03, 0.0, 0.25) == pytest.approx((up - dn) / 0.0002, rel=1e-4)
