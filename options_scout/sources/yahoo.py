"""Free fallback: Yahoo Finance via the `yfinance` package (pip install yfinance).

Quotes are delayed ~15 minutes and Yahoo's bid/ask are often 0 outside US market
hours, so run the scanner while the market is open. Yahoo has no implied-vol
history, so IV rank comes from the scanner's own daily snapshots (ivstore.py)
and fills in after you have run it on ~20 different days.
"""

from __future__ import annotations

import logging

import pandas as pd

from .base import normalize_chain

log = logging.getLogger(__name__)


class YahooSource:
    name = "yahoo"

    def __init__(self, yf_module=None):
        if yf_module is None:
            try:
                import yfinance as yf_module  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover - import guard
                raise RuntimeError("yfinance is not installed: pip install yfinance") from exc
        self.yf = yf_module
        self._tickers: dict = {}

    def _t(self, symbol: str):
        if symbol not in self._tickers:
            self._tickers[symbol] = self.yf.Ticker(symbol.replace(".", "-"))
        return self._tickers[symbol]

    def history(self, symbol: str, days: int) -> pd.DataFrame:
        years = max(1, -(-days // 252)) + 1
        df = self._t(symbol).history(period=f"{years}y", interval="1d", auto_adjust=True)
        if df is None or df.empty:
            raise LookupError(f"Yahoo returned no history for {symbol}")
        df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        return df[["open", "high", "low", "close", "volume"]].astype(float).tail(days)

    def spot(self, symbol: str) -> float | None:
        try:
            price = self._t(symbol).fast_info["last_price"]
            return float(price) if price else None
        except Exception as exc:  # noqa: BLE001 - yfinance raises many types
            log.debug("Yahoo spot for %s failed: %s", symbol, exc)
            return None

    def chain(self, symbol: str, min_dte: int, max_dte: int, lo_k: float, hi_k: float) -> pd.DataFrame:
        t = self._t(symbol)
        today = pd.Timestamp.today().normalize()
        spot = self.spot(symbol)
        frames = []
        for exp in t.options or ():
            dte = (pd.Timestamp(exp) - today).days
            if not min_dte <= dte <= max_dte:
                continue
            oc = t.option_chain(exp)
            for right, df in (("C", oc.calls), ("P", oc.puts)):
                if df is None or df.empty:
                    continue
                part = pd.DataFrame(
                    {"expiry": pd.Timestamp(exp), "strike": df["strike"], "right": right, "bid": df["bid"],
                     "ask": df["ask"], "last": df["lastPrice"], "volume": df.get("volume"),
                     "open_interest": df.get("openInterest"), "source_iv": df.get("impliedVolatility")}
                )
                if spot:
                    part = part[(part["strike"] >= spot * lo_k) & (part["strike"] <= spot * hi_k)]
                frames.append(part)
        return normalize_chain(pd.concat(frames, ignore_index=True) if frames else None)

    def iv_history(self, symbol: str):
        return None

    def dividend_yield(self, symbol: str) -> float | None:
        """Trailing 12-month dividends / price (Yahoo's own yield field changed units across versions)."""
        try:
            divs = self._t(symbol).dividends
            spot = self.spot(symbol)
            if divs is None or divs.empty or not spot:
                return 0.0
            idx = pd.to_datetime(divs.index).tz_localize(None)
            recent = divs[idx >= pd.Timestamp.today() - pd.Timedelta(days=365)]
            return float(recent.sum()) / spot
        except Exception as exc:  # noqa: BLE001
            log.debug("Yahoo dividends for %s failed: %s", symbol, exc)
            return None

    def close(self) -> None:
        pass
