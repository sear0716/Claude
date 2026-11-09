"""Yahoo Finance via yfinance: free, no account, quotes delayed about 15 minutes."""

from __future__ import annotations

import datetime as dt
import math

from ..models import Bar, OptionQuote, Snapshot


def _num(x, default=0.0) -> float:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(f) else f


def fetch(symbol: str, max_dte: int) -> Snapshot:
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("the yahoo provider needs yfinance: pip install -r cheap_options/requirements.txt") from exc

    ticker = yf.Ticker(symbol)
    hist = ticker.history(period="5y", interval="1d", auto_adjust=True)
    if hist.empty:
        raise RuntimeError(f"no price history for {symbol}")
    bars = bars_from_frame(hist)
    today = dt.date.today()
    quotes: list[OptionQuote] = []
    expirations = ticker.options
    if not expirations:
        raise RuntimeError("Yahoo returned no option chain (no listed options, or Yahoo is rate-limiting; retry shortly)")
    for exp in expirations:
        expiry = dt.date.fromisoformat(exp)
        if not 0 < (expiry - today).days <= max_dte:
            continue
        chain = ticker.option_chain(exp)
        quotes += quotes_from_frame(chain.calls, expiry, "C") + quotes_from_frame(chain.puts, expiry, "P")
    snap = Snapshot(symbol.upper(), today, bars[-1].close, bars, quotes)
    try:
        dates = (ticker.calendar or {}).get("Earnings Date") or []
        upcoming = [d for d in dates if isinstance(d, dt.date) and d >= today]
        snap.earnings_date = min(upcoming) if upcoming else None
    except Exception:  # earnings dates are a nice-to-have; Yahoo often has none
        pass
    return snap


def bars_from_frame(hist) -> list[Bar]:
    bars = []
    for ts, row in hist.iterrows():
        o, h, l, c = (_num(row[k]) for k in ("Open", "High", "Low", "Close"))
        if min(o, h, l, c) > 0:
            bars.append(Bar(ts.date(), o, h, l, c))
    return bars


def quotes_from_frame(frame, expiry: dt.date, right: str) -> list[OptionQuote]:
    return [
        OptionQuote(expiry, _num(r["strike"]), right, _num(r["bid"]), _num(r["ask"]),
                    int(_num(r.get("volume"))), int(_num(r.get("openInterest"))), str(r.get("contractSymbol", "")))
        for r in frame.to_dict("records")
    ]
