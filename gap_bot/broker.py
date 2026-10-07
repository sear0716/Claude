"""Thin wrapper around ib_async for the few calls the bot needs."""
import logging
import math

import pandas as pd
from ib_async import IB, MarketOrder, Stock, StopOrder, util

from .config import check_paper_accounts, check_paper_only
from .strategy import ET

log = logging.getLogger(__name__)
FILL_WAIT_SECONDS = 10


def ib_symbol(symbol):
    """'BRK.B' -> 'BRK B' (IBKR's format for class shares)."""
    return symbol.replace(".", " ").replace("-", " ")


def bars_to_df(bars):
    """ib_async BarData list -> DataFrame indexed by ET bar start time."""
    if not bars:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = util.df(bars)[["date", "open", "high", "low", "close", "volume"]]
    idx = pd.to_datetime(df["date"])
    if idx.dt.tz is None:
        idx = idx.dt.tz_localize(ET)
    df.index = pd.DatetimeIndex(idx.dt.tz_convert(ET))
    return df.drop(columns="date").astype(float)


class Broker:
    def __init__(self, settings, ib=None):
        check_paper_only(settings)
        self.settings = settings
        self.ib = ib or IB()
        self._contracts = {}

    # connection --------------------------------------------------------------
    def connect(self):
        s = self.settings
        self.ib.connect(s.host, s.port, clientId=s.client_id, timeout=10)
        try:
            check_paper_accounts(self.ib.managedAccounts())
        except Exception:
            self.ib.disconnect()
            raise
        return self

    def disconnect(self):
        self.ib.disconnect()

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.disconnect()

    # data --------------------------------------------------------------------
    def contract(self, symbol):
        if symbol not in self._contracts:
            c = Stock(ib_symbol(symbol), "SMART", "USD")
            qualified = self.ib.qualifyContracts(c)
            if not qualified or qualified[0] is None:
                raise LookupError(f"IBKR could not qualify {symbol}")
            self._contracts[symbol] = qualified[0]
        return self._contracts[symbol]

    def daily_bars(self, symbol, duration="1 Y"):
        bars = self.ib.reqHistoricalData(self.contract(symbol), endDateTime="", durationStr=duration,
                                         barSizeSetting="1 day", whatToShow="TRADES", useRTH=True, formatDate=1)
        df = bars_to_df(bars)
        if len(df):
            df.index = pd.DatetimeIndex([pd.Timestamp(d.date(), tz=ET) for d in df.index])
        return df

    def intraday_bars(self, symbol, duration="2 D"):
        """5-minute bars including premarket (useRTH=False)."""
        bars = self.ib.reqHistoricalData(self.contract(symbol), endDateTime="", durationStr=duration,
                                         barSizeSetting="5 mins", whatToShow="TRADES", useRTH=False, formatDate=2)
        return bars_to_df(bars)

    # account -----------------------------------------------------------------
    def positions(self):
        out = {}
        for p in self.ib.positions():
            if p.contract.secType == "STK" and p.position:
                out[p.contract.symbol.replace(" ", ".")] = p.position
        return out

    # orders ------------------------------------------------------------------
    def _wait(self, trade):
        waited = 0.0
        while not trade.isDone() and waited < FILL_WAIT_SECONDS:
            self.ib.sleep(0.5)
            waited += 0.5
        st = trade.orderStatus
        price = st.avgFillPrice if st.filled else math.nan
        return dict(order_id=trade.order.orderId, status=st.status, filled=int(st.filled), fill_price=price)

    def market(self, symbol, side, qty):
        trade = self.ib.placeOrder(self.contract(symbol), MarketOrder(side, qty, tif="DAY"))
        return self._wait(trade)

    def place_stop(self, symbol, qty, stop_price):
        trade = self.ib.placeOrder(self.contract(symbol), StopOrder("SELL", qty, round(stop_price, 2), tif="DAY"))
        self.ib.sleep(1)
        return trade.order.orderId

    def _open_trade(self, order_id):
        for t in self.ib.openTrades():
            if t.order.orderId == order_id:
                return t
        return None

    def modify_stop(self, order_id, qty=None, stop_price=None):
        t = self._open_trade(order_id)
        if t is None:
            return False
        if qty is not None:
            t.order.totalQuantity = qty
        if stop_price is not None:
            t.order.auxPrice = round(stop_price, 2)
        self.ib.placeOrder(t.contract, t.order)
        self.ib.sleep(1)
        return True

    def cancel(self, order_id):
        t = self._open_trade(order_id)
        if t is not None:
            self.ib.cancelOrder(t.order)
            self.ib.sleep(1)

    def stop_fill_price(self, order_id):
        """Average fill price if the stop order has executed today, else None.
        Matches on order id, never on quantity, so a partial sale isn't mistaken for a stop-out."""
        fills = [f for f in self.ib.reqExecutions() if f.execution.orderId == order_id]
        if not fills:
            return None
        shares = sum(f.execution.shares for f in fills)
        return sum(f.execution.shares * f.execution.price for f in fills) / shares
