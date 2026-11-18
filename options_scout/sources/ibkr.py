"""Interactive Brokers option chains, quotes and implied-vol history (read-only).

Reuses the repo's trading_agent IBKR connection (ib_async, readonly=True), so the
same TWS / IB Gateway setup applies. Nothing here can place an order.

Needs: OPRA options market data (or delayed data, IB_MARKET_DATA_TYPE=3) for
quotes, and US equity data for the stock's price history.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime

import pandas as pd

from trading_agent.config import Config
from trading_agent.sources.ibkr import IBKRSource, ib_symbol

from .base import normalize_chain

log = logging.getLogger(__name__)


def options_config() -> Config:
    """trading_agent's IB settings, with a client id of its own so both can run at once."""
    cfg = Config()
    cfg.ib_client_id = int(os.environ.get("OPTIONS_IB_CLIENT_ID", "19"))
    return cfg


def _num(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return math.nan
    return v if math.isfinite(v) and v >= 0 else math.nan


class IBKROptionsSource(IBKRSource):
    name = "ibkr"

    def __init__(self, cfg: Config | None = None, ib=None, market_data_type: int | None = None,
                 batch_size: int = 50, quote_wait: float = 3.0, max_expiries: int = 6):
        super().__init__(cfg or options_config(), ib=ib)
        # 1 live, 2 frozen, 3 delayed, 4 delayed-frozen
        self.market_data_type = market_data_type or int(os.environ.get("IB_MARKET_DATA_TYPE", "1"))
        self.batch_size = batch_size  # stays under IB's default 100 market-data lines
        self.quote_wait = quote_wait
        self.max_expiries = max_expiries
        self._stocks: dict = {}

    def _stock(self, symbol: str):
        if symbol not in self._stocks:
            from ib_async import Stock

            ib = self.connect()
            q = [c for c in ib.qualifyContracts(Stock(ib_symbol(symbol), "SMART", "USD")) if c is not None]
            if not q:
                raise LookupError(f"IB could not qualify {symbol}")
            self._stocks[symbol] = q[0]
        return self._stocks[symbol]

    def spot(self, symbol: str) -> float | None:
        ib = self.connect()
        ib.reqMarketDataType(self.market_data_type)
        [ticker] = ib.reqTickers(self._stock(symbol))
        price = _num(ticker.marketPrice())
        if math.isnan(price):
            price = _num(ticker.close)
        return None if math.isnan(price) else price

    def chain(self, symbol: str, min_dte: int, max_dte: int, lo_k: float, hi_k: float) -> pd.DataFrame:
        from ib_async import Option

        ib = self.connect()
        stock = self._stock(symbol)
        spot = self.spot(symbol) or float(self.history(symbol, 5)["close"].iloc[-1])
        params = ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)
        smart = [p for p in params if p.exchange == "SMART"] or list(params)
        if not smart:
            raise LookupError(f"IB returned no option chain for {symbol}")
        # Prefer the standard class (e.g. 'AAPL', not adjusted/mini classes).
        std = [p for p in smart if p.tradingClass == stock.symbol]
        chain = max(std or smart, key=lambda p: len(p.strikes))
        today = pd.Timestamp.today().normalize()
        expiries = [
            e for e in sorted(chain.expirations)
            if min_dte <= (pd.Timestamp(datetime.strptime(e, "%Y%m%d")) - today).days <= max_dte
        ][: self.max_expiries]
        strikes = [k for k in sorted(chain.strikes) if spot * lo_k <= k <= spot * hi_k]
        candidates = [
            Option(stock.symbol, e, k, right, "SMART", multiplier=chain.multiplier,
                   currency="USD", tradingClass=chain.tradingClass)
            for e in expiries for k in strikes for right in ("C", "P")
        ]
        # Not every strike is listed for every expiry: unqualifiable ones come back None/without conId.
        contracts = [c for c in ib.qualifyContracts(*candidates) if c is not None and getattr(c, "conId", 0)]
        log.info("%s: quoting %d option contracts", symbol, len(contracts))

        ib.reqMarketDataType(self.market_data_type)
        rows = []
        for i in range(0, len(contracts), self.batch_size):
            batch = contracts[i : i + self.batch_size]
            tickers = [ib.reqMktData(c, "101", False, False) for c in batch]  # 101 = open interest
            ib.sleep(self.quote_wait)
            for c, t in zip(batch, tickers):
                oi = _num(t.callOpenInterest if c.right == "C" else t.putOpenInterest)
                if math.isnan(oi):  # some ib_async versions/tick types land elsewhere
                    oi = _num(getattr(t, "openInterest", math.nan))
                greeks = t.modelGreeks
                rows.append({
                    "expiry": pd.Timestamp(datetime.strptime(c.lastTradeDateOrContractMonth, "%Y%m%d")),
                    "strike": c.strike, "right": c.right, "bid": _num(t.bid), "ask": _num(t.ask),
                    "last": _num(t.last), "volume": _num(t.volume), "open_interest": oi,
                    "source_iv": _num(greeks.impliedVol) if greeks else math.nan,
                })
                ib.cancelMktData(c)
        return normalize_chain(pd.DataFrame(rows))

    def iv_history(self, symbol: str) -> pd.Series | None:
        """IB's daily 30-day implied volatility index for the stock, last 12 months."""
        ib = self.connect()
        bars = ib.reqHistoricalData(
            self._stock(symbol), endDateTime="", durationStr="1 Y", barSizeSetting="1 day",
            whatToShow="OPTION_IMPLIED_VOLATILITY", useRTH=True, formatDate=1,
        )
        ib.sleep(self.cfg.ib_request_pause)
        if not bars:
            return None
        return pd.Series([b.close for b in bars], index=pd.to_datetime([b.date for b in bars]), dtype=float)

    def dividend_yield(self, symbol: str) -> float | None:
        return None  # not offered by the IB API without fundamentals subscriptions; pass --div-yield
