import numpy as np
import pandas as pd
import pytest

from trading_agent.config import Config
from trading_agent.indicators import atr, compute_all, macd, rsi, sma, wilder_smooth


def test_sma_simple_average():
    s = pd.Series([1.0, 2, 3, 4, 5])
    out = sma(s, 3)
    assert out.isna().sum() == 2
    assert out.tolist()[2:] == [2.0, 3.0, 4.0]


def test_wilder_smooth_seed_and_recursion():
    s = pd.Series([1.0, 2, 3, 4, 5, 6])
    out = wilder_smooth(s, 3)
    assert np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)  # seed = mean(1, 2, 3)
    assert out.iloc[3] == pytest.approx((2.0 * 2 + 4) / 3)


def test_rsi_extremes():
    up = pd.Series(np.arange(1.0, 40.0))
    flat = pd.Series([10.0] * 40)
    assert rsi(up, 14).iloc[-1] == pytest.approx(100.0)
    assert rsi(flat, 14).iloc[-1] == pytest.approx(50.0)
    assert rsi(up[::-1].reset_index(drop=True), 14).iloc[-1] == pytest.approx(0.0)


def test_rsi_matches_hand_computed_wilder():
    close = pd.Series([44.0, 45, 44, 46, 47, 46, 48])
    # period 3: deltas +1 -1 +2 +1 -1 +2
    # seed gains mean(1,0,2)=1, losses mean(0,1,0)=1/3
    # next: gain (1*2+1)/3=1, loss (1/3*2+0)/3=2/9 ; then gain 2/3, loss (4/9+1)/3=13/27
    # then gain (4/3+2)/3=10/9, loss 26/81
    out = rsi(close, 3)
    rs = (10 / 9) / (26 / 81)
    assert out.iloc[-1] == pytest.approx(100 - 100 / (1 + rs))


def test_macd_of_constant_is_zero():
    m = macd(pd.Series([50.0] * 60))
    assert m.iloc[-1].abs().max() == pytest.approx(0.0)


def test_macd_positive_in_uptrend():
    m = macd(pd.Series(np.linspace(10, 50, 80)))
    assert m["macd"].iloc[-1] > 0


def test_atr_constant_range():
    n = 30
    close = pd.Series([100.0] * n)
    out = atr(close + 2, close - 2, close, 14)
    assert out.iloc[-1] == pytest.approx(4.0)
    assert out.iloc[:14].isna().all()


def test_atr_uses_gaps_from_previous_close():
    close = pd.Series([100.0, 110.0, 110.0])
    high = pd.Series([101.0, 111.0, 111.0])
    low = pd.Series([99.0, 109.0, 109.0])
    # bar 1 true range = max(2, |111-100|, |109-100|) = 11, bar 2 = 2
    assert atr(high, low, close, 2).iloc[-1] == pytest.approx((11 + 2) / 2)


def test_compute_all_columns():
    idx = pd.bdate_range("2024-01-01", periods=260)
    close = pd.Series(np.linspace(100, 150, 260), index=idx)
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1e6})
    out = compute_all(df, Config())
    for col in ("sma_fast", "sma_slow", "rsi", "macd", "macd_signal", "macd_hist", "atr", "chandelier_stop"):
        assert col in out and not np.isnan(out[col].iloc[-1])
