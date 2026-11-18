import numpy as np
import pandas as pd
import pytest

from options_scout.volatility import VolProfile, close_to_close, ewma, iv_rank, parkinson, vol_profile, yang_zhang


def gbm_ohlc(sigma=0.3, n=2000, steps=78, seed=1):
    """Daily OHLC built from an intraday GBM path, so range estimators are well defined."""
    rng = np.random.default_rng(seed)
    dt = 1 / (252 * steps)
    path = np.exp(np.cumsum(rng.normal(-0.5 * sigma**2 * dt, sigma * np.sqrt(dt), n * steps))) * 100
    days = path.reshape(n, steps)
    idx = pd.bdate_range(end="2026-10-30", periods=n)
    return pd.DataFrame({"open": days[:, 0], "high": days.max(1), "low": days.min(1), "close": days[:, -1],
                         "volume": 1.0}, index=idx)


@pytest.mark.parametrize("estimator", [close_to_close, parkinson, yang_zhang])
def test_estimators_recover_true_vol(estimator):
    df = gbm_ohlc(0.3)
    # discrete sampling biases range estimators slightly low; 10% tolerance is plenty to catch formula errors
    assert estimator(df, 1500) == pytest.approx(0.3, rel=0.10)


def test_ewma_close_to_true_vol():
    assert ewma(gbm_ohlc(0.25, n=600)) == pytest.approx(0.25, rel=0.35)


def test_forecast_mean_reverts_between_current_and_long_run():
    p = VolProfile(*([0.2] * 7), current=0.40, long_run=0.20, half_life_days=30)
    short, mid, long = p.forecast(1), p.forecast(30), p.forecast(2000)
    assert short == pytest.approx(0.40, abs=0.01)
    assert 0.20 < long < mid < short
    assert long == pytest.approx(0.20, abs=0.02)


def test_vol_profile_fields():
    prof = vol_profile(gbm_ohlc(0.3, n=400))
    assert 0.2 < prof.current < 0.4 and 0.2 < prof.long_run < 0.4


def test_iv_rank_and_percentile():
    hist = pd.Series(np.linspace(0.2, 0.4, 101))
    rank, pct = iv_rank(hist, 0.25)
    assert rank == pytest.approx(25.0)
    assert pct == pytest.approx(25 / 101 * 100, abs=0.1)
    assert iv_rank(hist.head(10), 0.25) == (None, None)
