"""Offline tests for ib_history.py: a fake IB stands in for TWS."""
import csv
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import ib_history
from ib_async import BarData


class FakeIB:
    def __init__(self, bars_by_symbol):
        self.bars_by_symbol = bars_by_symbol
        self.connect_kwargs = None
        self.requests = []
        self.disconnected = False

    def connect(self, host, port, **kwargs):
        self.connect_kwargs = dict(host=host, port=port, **kwargs)

    def qualifyContracts(self, contract):
        if contract.symbol not in self.bars_by_symbol:
            return [None]
        contract.conId = 1
        return [contract]

    def reqHistoricalData(self, contract, **kwargs):
        self.requests.append((contract.symbol, kwargs))
        return self.bars_by_symbol[contract.symbol]

    def disconnect(self):
        self.disconnected = True


def bar(day, close):
    return BarData(date=dt.date(2026, 10, day), open=close - 1, high=close + 1,
                   low=close - 2, close=close, volume=1000, average=close, barCount=10)


def test_writes_csv_with_defaults(tmp_path):
    ib = FakeIB({"AAPL": [bar(1, 250.0), bar(2, 252.5)]})
    rc = ib_history.main(["AAPL", "--out-dir", str(tmp_path)], ib=ib)

    assert rc == 0
    assert ib.connect_kwargs["readonly"] is True
    assert ib.connect_kwargs["port"] == 7497
    assert ib.disconnected
    _, req = ib.requests[0]
    assert req["durationStr"] == "1 Y" and req["barSizeSetting"] == "1 day"
    assert req["whatToShow"] == "TRADES" and req["useRTH"] is True

    rows = list(csv.DictReader((tmp_path / "AAPL_1day_1Y.csv").open()))
    assert [r["date"] for r in rows] == ["2026-10-01", "2026-10-02"]
    assert rows[-1]["close"] == "252.5"


def test_share_class_symbol_and_unknown_symbol(tmp_path, capsys):
    ib = FakeIB({"BRK B": [bar(1, 480.0)]})
    rc = ib_history.main(["brk.b", "NOPE", "--duration", "6 M", "--bar-size", "1 hour",
                          "--out-dir", str(tmp_path)], ib=ib)

    assert rc == 1  # NOPE failed
    assert (tmp_path / "BRKB_1hour_6M.csv").exists()
    assert "NOPE: IB could not find" in capsys.readouterr().out
    assert ib.disconnected
