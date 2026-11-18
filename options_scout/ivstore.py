"""Local daily log of each stock's 30-day ATM implied vol, used for IV rank when
the data source has no IV history (Yahoo). One CSV per symbol."""

from __future__ import annotations

import math
import os
from pathlib import Path

import pandas as pd


def default_dir() -> Path:
    base = os.environ.get("OPTIONS_SCOUT_CACHE") or Path.home() / ".cache" / "options_scout"
    return Path(base) / "iv_history"


class IVStore:
    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory) if directory else default_dir()

    def _path(self, symbol: str) -> Path:
        return self.directory / f"{symbol.upper()}.csv"

    def load(self, symbol: str) -> pd.Series:
        path = self._path(symbol)
        if not path.exists():
            return pd.Series(dtype=float)
        df = pd.read_csv(path, parse_dates=["date"])
        return df.set_index("date")["atm_iv30"].astype(float).sort_index()

    def record(self, symbol: str, day: pd.Timestamp, atm_iv30: float) -> None:
        if atm_iv30 is None or not math.isfinite(atm_iv30):
            return
        s = self.load(symbol)
        s.loc[pd.Timestamp(day).normalize()] = float(atm_iv30)  # one reading per day, latest wins
        self.directory.mkdir(parents=True, exist_ok=True)
        s.sort_index().rename("atm_iv30").rename_axis("date").to_csv(self._path(symbol))
