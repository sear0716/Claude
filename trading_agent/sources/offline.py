"""Price sources that work without a broker connection.

CSVSource  - reads <dir>/<SYMBOL>.csv with date,open,high,low,close[,volume] columns.
DemoSource - deterministic synthetic random walks. For trying the pipeline only:
             its output is NOT market data and must not be traded on.
"""

from __future__ import annotations

import zlib
from pathlib import Path

import numpy as np
import pandas as pd


class CSVSource:
    name = "csv"

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def history(self, symbol: str, days: int) -> pd.DataFrame:
        path = self.directory / f"{symbol.upper()}.csv"
        if not path.exists():
            path = self.directory / f"{symbol.upper().replace('.', '-')}.csv"
        df = pd.read_csv(path)
        df.columns = [c.strip().lower() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        if "volume" not in df:
            df["volume"] = 0.0
        return df[["open", "high", "low", "close", "volume"]].astype(float).tail(days)


class DemoSource:
    name = "demo"

    def history(self, symbol: str, days: int) -> pd.DataFrame:
        rng = np.random.default_rng(zlib.crc32(symbol.encode()))
        drift = rng.normal(0.0004, 0.0008)
        vol = rng.uniform(0.012, 0.025)
        returns = rng.normal(drift, vol, days)
        close = 100 * rng.uniform(0.5, 4) * np.exp(np.cumsum(returns))
        spread = np.abs(rng.normal(0, vol, days)) * close
        open_ = close * (1 + rng.normal(0, vol / 3, days))
        high = np.maximum(open_, close) + spread / 2
        low = np.minimum(open_, close) - spread / 2
        index = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
        return pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": rng.integers(1e6, 5e7, days)},
            index=index,
        ).astype(float)
