"""Data sources. Each exposes fetch(symbol, max_dte) -> Snapshot."""

from __future__ import annotations

from ..models import Snapshot


def fetch(provider: str, symbol: str, max_dte: int) -> Snapshot:
    if provider == "yahoo":
        from . import yahoo
        return yahoo.fetch(symbol, max_dte)
    if provider == "schwab":
        from . import schwab
        return schwab.fetch(symbol, max_dte)
    raise ValueError(f"unknown provider {provider!r}")
