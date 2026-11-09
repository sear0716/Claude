"""Schwab market data through this repo's schwab_mcp client (real-time, needs its OAuth setup)."""

from __future__ import annotations

import asyncio
import datetime as dt

from ..models import Bar, OptionQuote, Snapshot


def fetch(symbol: str, max_dte: int) -> Snapshot:
    return asyncio.run(_fetch(symbol.upper(), max_dte))


async def _fetch(symbol: str, max_dte: int) -> Snapshot:
    from schwab_mcp.client import SchwabClient
    from schwab_mcp.config import load_settings
    from schwab_mcp.tokens import make_store
    from schwab_mcp.oauth import TokenManager

    settings = load_settings()
    client = SchwabClient(TokenManager(settings, make_store(settings)))
    today = dt.date.today()
    try:
        chain = await client.get("/marketdata/v1/chains", {
            "symbol": symbol, "contractType": "ALL", "range": "ALL", "includeUnderlyingQuote": "true",
            "fromDate": today.isoformat(), "toDate": (today + dt.timedelta(days=max_dte)).isoformat(),
        })
        history = await client.get("/marketdata/v1/pricehistory", {
            "symbol": symbol, "periodType": "year", "period": 5, "frequencyType": "daily", "frequency": 1,
        })
    finally:
        await client.aclose()
    return parse(symbol, today, chain or {}, history or {})


def parse(symbol: str, asof: dt.date, chain: dict, history: dict) -> Snapshot:
    if chain.get("status") == "FAILED":
        raise RuntimeError(f"Schwab has no option chain for {symbol}")
    bars = []
    for c in history.get("candles", []):
        day = dt.datetime.fromtimestamp(c["datetime"] / 1000, dt.timezone.utc).date()
        if min(c["open"], c["high"], c["low"], c["close"]) > 0:
            bars.append(Bar(day, c["open"], c["high"], c["low"], c["close"]))
    quotes = []
    for key, right in (("callExpDateMap", "C"), ("putExpDateMap", "P")):
        for exp_key, strikes in (chain.get(key) or {}).items():
            expiry = dt.date.fromisoformat(exp_key.split(":")[0])
            for contracts in strikes.values():
                for c in contracts:
                    quotes.append(OptionQuote(
                        expiry, float(c["strikePrice"]), right, float(c.get("bid") or 0), float(c.get("ask") or 0),
                        int(c.get("totalVolume") or 0), int(c.get("openInterest") or 0), c.get("symbol", "")))
    spot = chain.get("underlyingPrice") or (bars[-1].close if bars else 0.0)
    return Snapshot(symbol, asof, float(spot), bars, quotes)
