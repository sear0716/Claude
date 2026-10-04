"""Interactive Brokers: account values, positions and daily price history.

Uses `ib_async` (the maintained successor of ib_insync) against a running TWS or
IB Gateway. The connection is opened read-only: this agent never places orders.
Historical data needs a market-data subscription for the requested symbols.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class Holding:
    symbol: str
    quantity: float
    avg_cost: float
    account: str


def ib_symbol(symbol: str) -> str:
    """IB writes share classes with a space: BRK.B / BRK-B -> 'BRK B'."""
    return symbol.replace(".", " ").replace("-", " ")


class IBKRSource:
    name = "ibkr"

    def __init__(self, cfg, ib=None):
        self.cfg = cfg
        self._ib = ib

    # -- connection -----------------------------------------------------------------
    def connect(self):
        if self._ib is not None and self._ib.isConnected():
            return self._ib
        try:
            from ib_async import IB
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError("ib_async is not installed: pip install ib_async") from exc
        ib = self._ib or IB()
        ib.connect(
            self.cfg.ib_host,
            self.cfg.ib_port,
            clientId=self.cfg.ib_client_id,
            readonly=True,
            timeout=15,
        )
        self._ib = ib
        return ib

    def close(self) -> None:
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()

    # -- account --------------------------------------------------------------------
    def account_summary(self) -> dict[str, float]:
        ib = self.connect()
        wanted = {"NetLiquidation", "TotalCashValue", "BuyingPower", "AvailableFunds", "GrossPositionValue"}
        summary: dict[str, float] = {}
        for item in ib.accountSummary(self.cfg.ib_account or ""):
            if item.tag in wanted and item.currency in ("USD", "BASE", ""):
                try:
                    summary[item.tag] = float(item.value)
                except ValueError:
                    continue
        return summary

    def positions(self) -> list[Holding]:
        ib = self.connect()
        holdings = []
        for pos in ib.positions(self.cfg.ib_account or ""):
            if pos.contract.secType != "STK" or not pos.position:
                continue
            holdings.append(
                Holding(
                    symbol=pos.contract.symbol.replace(" ", "."),
                    quantity=float(pos.position),
                    avg_cost=float(pos.avgCost),
                    account=pos.account,
                )
            )
        return holdings

    # -- prices ---------------------------------------------------------------------
    def history(self, symbol: str, days: int) -> pd.DataFrame:
        from ib_async import Stock, util

        ib = self.connect()
        contract = Stock(ib_symbol(symbol), "SMART", "USD")
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise LookupError(f"IB could not qualify contract for {symbol}")
        years = max(1, -(-days // 252))  # ceil(trading days / 252)
        bars = ib.reqHistoricalData(
            qualified[0],
            endDateTime="",
            durationStr=f"{years + 1} Y",
            barSizeSetting="1 day",
            whatToShow="ADJUSTED_LAST",  # split/dividend adjusted, so long SMAs stay valid
            useRTH=True,
            formatDate=1,
        )
        time.sleep(self.cfg.ib_request_pause)
        if not bars:
            raise LookupError(f"IB returned no history for {symbol}")
        df = util.df(bars)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")[["open", "high", "low", "close", "volume"]].astype(float)
        return df.tail(days)
