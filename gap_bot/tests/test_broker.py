import datetime as dt

import pytest
from ib_async import BarData

from gap_bot.broker import Broker, bars_to_df, ib_symbol
from gap_bot.config import PaperOnlyError, Settings
from gap_bot.strategy import ET


class FakeIB:
    def __init__(self, accounts):
        self.accounts = accounts
        self.disconnected = False

    def connect(self, host, port, **kw):
        self.port = port

    def managedAccounts(self):
        return self.accounts

    def disconnect(self):
        self.disconnected = True


def test_refuses_live_account():
    ib = FakeIB(["U1234567"])
    with pytest.raises(PaperOnlyError):
        Broker(Settings(), ib=ib).connect()
    assert ib.disconnected


def test_refuses_live_port_before_connecting():
    with pytest.raises(PaperOnlyError):
        Broker(Settings(port=7496), ib=FakeIB(["DU1"]))


def test_paper_account_connects():
    b = Broker(Settings(), ib=FakeIB(["DU1234567"])).connect()
    assert b.ib.port == 7497


def test_ib_symbol():
    assert ib_symbol("BRK.B") == "BRK B"


def test_bars_to_df_converts_utc_to_eastern():
    t = dt.datetime(2026, 10, 5, 14, 30, tzinfo=dt.timezone.utc)
    df = bars_to_df([BarData(date=t, open=1, high=2, low=0.5, close=1.5, volume=100, average=1, barCount=3)])
    assert df.index[0] == dt.datetime(2026, 10, 5, 10, 30, tzinfo=ET)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
