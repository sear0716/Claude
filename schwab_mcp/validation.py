"""Input validation done before any request reaches Schwab."""

from __future__ import annotations

import datetime as dt
import re

from .errors import InvalidInput

# Equities (BRK.B, BF/B), indexes ($SPX), futures (/ES), and OCC option symbols
# ("AAPL  250117C00150000"), which contain spaces.
SYMBOL_RE = re.compile(r"^[A-Z0-9$/^.][A-Z0-9 $/^._-]{0,31}$")
MAX_QUOTE_SYMBOLS = 50

# Allowed periodType -> periods, and periodType -> frequencyTypes (Schwab price history rules).
PERIODS = {
    "day": (1, 2, 3, 4, 5, 10),
    "month": (1, 2, 3, 6),
    "year": (1, 2, 3, 5, 10, 15, 20),
    "ytd": (1,),
}
FREQUENCY_TYPES = {
    "day": ("minute",),
    "month": ("daily", "weekly"),
    "year": ("daily", "weekly", "monthly"),
    "ytd": ("daily", "weekly"),
}
FREQUENCIES = {"minute": (1, 5, 10, 15, 30), "daily": (1,), "weekly": (1,), "monthly": (1,)}
MARKETS = ("equity", "option", "bond", "future", "forex")
CONTRACT_TYPES = ("ALL", "CALL", "PUT")
ORDER_STATUSES = ("WORKING", "FILLED", "CANCELED", "REJECTED", "QUEUED", "PENDING_ACTIVATION", "EXPIRED", "ACCEPTED", "REPLACED")
STRIKE_RANGES = ("ALL", "ITM", "NTM", "OTM", "SAK", "SBK", "SNK")


def symbol(value: str) -> str:
    s = (value or "").strip().upper()
    if not SYMBOL_RE.match(s):
        raise InvalidInput(f"Invalid symbol {value!r}. Use tickers like AAPL, BRK.B, $SPX or /ES.")
    return s


def symbols(values: list[str] | str) -> list[str]:
    if isinstance(values, str):
        values = [v for v in re.split(r"[,\s]+", values) if v]
    out: list[str] = []
    for v in values:
        s = symbol(v)
        if s not in out:
            out.append(s)
    if not out:
        raise InvalidInput("Provide at least one symbol.")
    if len(out) > MAX_QUOTE_SYMBOLS:
        raise InvalidInput(f"At most {MAX_QUOTE_SYMBOLS} symbols per call.")
    return out


def date(value: str | None, name: str) -> dt.date | None:
    if value in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        raise InvalidInput(f"{name} must be a date like 2025-01-31, got {value!r}.") from None


def date_range(start: dt.date | None, end: dt.date | None, *, allow_future: bool = False) -> None:
    today = dt.date.today()
    if start and end and start > end:
        raise InvalidInput("start_date must be on or before end_date.")
    if not allow_future:
        for d, name in ((start, "start_date"), (end, "end_date")):
            if d and d > today:
                raise InvalidInput(f"{name} {d} is in the future.")


def price_history_params(
    period_type: str, period: int | None, frequency_type: str | None, frequency: int
) -> tuple[str, str]:
    pt = (period_type or "").lower()
    if pt not in PERIODS:
        raise InvalidInput(f"period_type must be one of {', '.join(PERIODS)}.")
    if period is not None and period not in PERIODS[pt]:
        raise InvalidInput(f"For period_type={pt}, period must be one of {PERIODS[pt]}.")
    ft = (frequency_type or FREQUENCY_TYPES[pt][0]).lower()
    if ft not in FREQUENCY_TYPES[pt]:
        raise InvalidInput(f"For period_type={pt}, frequency_type must be one of {FREQUENCY_TYPES[pt]}.")
    if frequency not in FREQUENCIES[ft]:
        raise InvalidInput(f"For frequency_type={ft}, frequency must be one of {FREQUENCIES[ft]}.")
    return pt, ft


def choice(value: str, allowed: tuple[str, ...], name: str, *, upper: bool = True) -> str:
    v = (value or "").strip()
    v = v.upper() if upper else v.lower()
    if v not in allowed:
        raise InvalidInput(f"{name} must be one of {', '.join(allowed)}.")
    return v


def int_range(value: int, lo: int, hi: int, name: str) -> int:
    if not isinstance(value, int) or not lo <= value <= hi:
        raise InvalidInput(f"{name} must be an integer between {lo} and {hi}.")
    return value
