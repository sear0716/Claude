"""Plain data containers shared by the providers and the analysis."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Bar:
    date: dt.date
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class OptionQuote:
    expiry: dt.date
    strike: float
    right: str  # "C" or "P"
    bid: float
    ask: float
    volume: int = 0
    open_interest: int = 0
    symbol: str = ""

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid + self.ask)

    @property
    def spread_pct(self) -> float:
        return (self.ask - self.bid) / self.mid if self.mid > 0 else float("inf")


@dataclass
class Snapshot:
    """Everything the analysis needs for one underlying at one moment."""

    symbol: str
    asof: dt.date
    spot: float
    bars: list[Bar]  # daily, oldest first, split/dividend adjusted
    quotes: list[OptionQuote]
    earnings_date: dt.date | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "asof": self.asof.isoformat(),
            "spot": self.spot,
            "earnings_date": self.earnings_date.isoformat() if self.earnings_date else None,
            "bars": [[b.date.isoformat(), b.open, b.high, b.low, b.close] for b in self.bars],
            "quotes": [
                [q.expiry.isoformat(), q.strike, q.right, q.bid, q.ask, q.volume, q.open_interest, q.symbol]
                for q in self.quotes
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Snapshot":
        return cls(
            symbol=d["symbol"],
            asof=dt.date.fromisoformat(d["asof"]),
            spot=float(d["spot"]),
            earnings_date=dt.date.fromisoformat(d["earnings_date"]) if d.get("earnings_date") else None,
            bars=[Bar(dt.date.fromisoformat(b[0]), *map(float, b[1:5])) for b in d["bars"]],
            quotes=[
                OptionQuote(dt.date.fromisoformat(q[0]), float(q[1]), q[2], float(q[3]), float(q[4]),
                            int(q[5]), int(q[6]), q[7] if len(q) > 7 else "")
                for q in d["quotes"]
            ],
        )
