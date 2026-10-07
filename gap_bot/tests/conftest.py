import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gap_bot.config import load_rules  # noqa: E402
from gap_bot.strategy import ET  # noqa: E402

DAY = pd.Timestamp("2026-10-05").date()  # a Monday


@pytest.fixture
def rules():
    return load_rules()


def make_daily(n=220, end=DAY, start_price=50.0, volume=1_000_000):
    """Steady uptrend so the previous close sits above the 200-day SMA."""
    dates = pd.bdate_range(end=pd.Timestamp(end) - pd.Timedelta(days=1), periods=n)
    close = np.linspace(start_price * 0.7, start_price, n)
    df = pd.DataFrame({"open": close - 0.2, "high": close + 0.5, "low": close - 0.5,
                       "close": close, "volume": float(volume)},
                      index=pd.DatetimeIndex([pd.Timestamp(d.date(), tz=ET) for d in dates]))
    return df


def make_intraday(day=DAY, prev_close=50.0, path=None, volume=60_000):
    """Premarket 08:00-09:25 then RTH 09:30-15:55 5-min bars following `path` (list of closes)."""
    pre_idx = pd.date_range(pd.Timestamp(f"{day} 08:00", tz=ET), periods=18, freq="5min")
    pre = pd.DataFrame({"open": 52.0, "high": 52.4, "low": 51.8, "close": 52.2, "volume": 5_000.0}, index=pre_idx)
    rth_idx = pd.date_range(pd.Timestamp(f"{day} 09:30", tz=ET), periods=78, freq="5min")
    path = path if path is not None else default_path()
    closes = np.array(path[: len(rth_idx)] + [path[-1]] * (len(rth_idx) - len(path)), dtype=float)
    opens = np.concatenate([[52.0], closes[:-1]])
    rth = pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) + 0.05,
                        "low": np.minimum(opens, closes) - 0.05, "close": closes, "volume": float(volume)},
                       index=rth_idx)
    return pd.concat([pre, rth])


def default_path():
    """Gap up to 52, chop until ~10:00, break out to new highs, pull back (swing low), run to 56."""
    p = [51.9, 51.7, 51.6, 51.8, 52.0, 52.3, 52.35, 52.4]          # 09:30-10:05
    p += [52.6, 52.8, 53.0, 53.2, 53.4, 53.6, 53.8, 54.0]           # breakout
    p += [53.9, 53.85, 54.1, 54.4, 54.8, 55.2, 55.6, 56.0]          # swing low then higher
    return p
