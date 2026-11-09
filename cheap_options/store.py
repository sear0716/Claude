"""Local record of each symbol's 30-day ATM implied vol, one row per day.

Free data sources do not publish historical implied volatility, so IV rank and percentile can only
be exact once the scanner has been run for a while. Until then the analysis falls back to a
realised-volatility proxy and says so.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

DEFAULT_PATH = Path.home() / ".cheap_options" / "iv_history.sqlite"


class IvStore:
    def __init__(self, path: Path = DEFAULT_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS iv30 (symbol TEXT, day TEXT, iv REAL, PRIMARY KEY (symbol, day))")

    def record(self, symbol: str, day: dt.date, iv: float) -> None:
        self.db.execute("INSERT OR REPLACE INTO iv30 VALUES (?, ?, ?)", (symbol.upper(), day.isoformat(), iv))
        self.db.commit()

    def history(self, symbol: str, before: dt.date) -> list[float]:
        """Past values, oldest first, excluding `before` itself so today is ranked against the past."""
        rows = self.db.execute("SELECT iv FROM iv30 WHERE symbol = ? AND day < ? ORDER BY day",
                               (symbol.upper(), before.isoformat()))
        return [r[0] for r in rows]

    def close(self) -> None:
        self.db.close()
