"""Morning scan: S&P 500 names gapping up >= min_gap_pct, written to state/watchlist.txt."""
import logging

import pandas as pd

from .config import PKG_DIR

log = logging.getLogger(__name__)


def load_universe(path=None):
    lines = (path or PKG_DIR / "sp500.txt").read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def yahoo_symbol(symbol):
    """'BRK.B' / 'BRK B' -> 'BRK-B' (Yahoo's format)."""
    return symbol.replace(".", "-").replace(" ", "-")


def scan_gaps(daily_by_symbol, rules):
    """daily_by_symbol: {symbol: DataFrame with >= 2 daily rows (open, close)}.
    Returns survivors sorted by gap, largest first, capped at max_watchlist."""
    u = rules["universe"]
    rows = []
    for sym, df in daily_by_symbol.items():
        df = df.dropna(subset=["open", "close"])
        if len(df) < 2:
            continue
        prev_close, today_open, price = df["close"].iloc[-2], df["open"].iloc[-1], df["close"].iloc[-1]
        gap = (today_open / prev_close - 1) * 100
        if gap >= u["min_gap_pct"] and price >= u["min_price"]:
            rows.append(dict(symbol=sym, gap_pct=float(gap), price=float(price)))
    rows.sort(key=lambda r: r["gap_pct"], reverse=True)
    return rows[: u["max_watchlist"]]


def download_daily(symbols, period="5d"):
    import yfinance as yf

    ymap = {yahoo_symbol(s): s for s in symbols}
    raw = yf.download(list(ymap), period=period, interval="1d", group_by="ticker",
                      threads=5, progress=False, auto_adjust=False)
    out = {}
    for ysym, sym in ymap.items():
        try:
            df = raw[ysym] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        df = df.rename(columns=str.lower)
        if {"open", "close"} <= set(df.columns):
            out[sym] = df
    return out


def run_prefilter(state, rules, universe=None):
    symbols = universe or load_universe()
    data = download_daily(symbols)
    failed = len(symbols) - sum(1 for df in data.values() if len(df.dropna(subset=["close"])) >= 2)
    survivors = scan_gaps(data, rules)
    state.write_watchlist(survivors)
    summary = dict(scanned=len(symbols), failed_downloads=failed, survivors=len(survivors),
                   watchlist=[r["symbol"] for r in survivors])
    if symbols and failed / len(symbols) > 0.30:
        summary["tripwire"] = f"{failed}/{len(symbols)} downloads failed - check network/yfinance"
    return summary
