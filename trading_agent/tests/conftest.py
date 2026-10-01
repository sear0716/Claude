import numpy as np
import pandas as pd
import pytest


def make_ohlc(close) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    idx = pd.bdate_range(end="2026-09-30", periods=len(close))
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1e6},
        index=idx,
    )


def wavy_trend(start: float, end: float, n: int = 320, amp: float = 0.02, period: int = 25) -> np.ndarray:
    base = np.linspace(start, end, n)
    return base * (1 + amp * np.sin(np.arange(n) * 2 * np.pi / period))


@pytest.fixture
def uptrend_df():
    return make_ohlc(wavy_trend(100, 180))


@pytest.fixture
def downtrend_df():
    return make_ohlc(wavy_trend(180, 100))
