import pandas as pd

from gap_bot.config import Settings
from gap_bot.cycle import run_cycle
from gap_bot.perf import round_trips_from_fills, summarize
from gap_bot.state import State
from gap_bot.strategy import ET

from .conftest import DAY, make_daily, make_intraday


class FakeBroker:
    def __init__(self, intraday):
        self.intraday = intraday
        self.orders = []
        self.held = {}
        self.stops = {}
        self.stop_fills = {}
        self.next_id = 100

    def daily_bars(self, symbol, duration="1 Y"):
        return make_daily()

    def intraday_bars(self, symbol, duration="2 D"):
        return self.intraday

    def positions(self):
        return dict(self.held)

    def market(self, symbol, side, qty):
        self.next_id += 1
        px = 52.6 if side == "BUY" else 53.9
        self.held[symbol] = self.held.get(symbol, 0) + (qty if side == "BUY" else -qty)
        self.orders.append((side, symbol, qty))
        return dict(order_id=self.next_id, status="Filled", filled=qty, fill_price=px)

    def place_stop(self, symbol, qty, stop):
        self.next_id += 1
        self.stops[self.next_id] = [symbol, qty, stop]
        return self.next_id

    def modify_stop(self, order_id, qty=None, stop_price=None):
        if qty is not None:
            self.stops[order_id][1] = qty
        if stop_price is not None:
            self.stops[order_id][2] = stop_price
        return True

    def cancel(self, order_id):
        self.stops.pop(order_id, None)

    def stop_fill_price(self, order_id):
        return self.stop_fills.get(order_id)


def at(hhmm):
    return pd.Timestamp(f"{DAY} {hhmm}", tz=ET).to_pydatetime()


def test_cycle_enters_manages_and_force_closes(rules, tmp_path):
    settings = Settings(rules=rules, state_dir=tmp_path / "state", log_dir=tmp_path / "logs")
    state = State(settings.state_dir, settings.log_dir)
    state.write_watchlist([dict(symbol="XYZ", gap_pct=4.0, price=52.4)])
    broker = FakeBroker(make_intraday())

    s = run_cycle(settings, broker, state, now=at("10:10:10"))
    assert s["entries"] == []                          # not above premarket high yet
    s = run_cycle(settings, broker, state, now=at("10:15:10"))
    assert s["entries"][0][:2] == ("XYZ", 47)
    pos = state.load_positions()["XYZ"]
    assert broker.stops[pos.stop_order_id] == ["XYZ", 47, pos.initial_stop]

    run_cycle(settings, broker, state, now=at("10:15:20"))  # same bar again: no double entry
    assert [o[0] for o in broker.orders] == ["BUY"]

    run_cycle(settings, broker, state, now=at("11:30:10"))  # well past 1R
    pos = state.load_positions()["XYZ"]
    assert pos.partial_done and pos.breakeven_done
    assert pos.qty == 32 and broker.stops[pos.stop_order_id][1] == 32
    assert broker.stops[pos.stop_order_id][2] >= pos.entry_price

    s = run_cycle(settings, broker, state, now=at("15:52"))
    assert s["gate"] == "force_close" and state.load_positions() == {}
    assert broker.held["XYZ"] == 0

    trips, still_open = round_trips_from_fills(state.read_trades())
    assert len(trips) == 1 and not still_open
    assert trips[0]["exit_reason"] == "partial+force_close"
    assert summarize(trips)["trades"] == 1


def test_cycle_detects_stop_out(rules, tmp_path):
    settings = Settings(rules=rules, state_dir=tmp_path / "state", log_dir=tmp_path / "logs")
    state = State(settings.state_dir, settings.log_dir)
    state.write_watchlist([dict(symbol="XYZ", gap_pct=4.0, price=52.4)])
    broker = FakeBroker(make_intraday())
    run_cycle(settings, broker, state, now=at("10:15:10"))
    pos = state.load_positions()["XYZ"]
    broker.stop_fills[pos.stop_order_id] = 51.0
    broker.held["XYZ"] = 0
    s = run_cycle(settings, broker, state, now=at("10:20:10"))
    assert s["exits"] == [("XYZ", "stop", 51.0)]
    assert state.load_positions() == {}


def test_dry_run_places_no_orders(rules, tmp_path):
    settings = Settings(rules=rules, state_dir=tmp_path / "state", log_dir=tmp_path / "logs")
    state = State(settings.state_dir, settings.log_dir)
    state.write_watchlist([dict(symbol="XYZ", gap_pct=4.0, price=52.4)])
    broker = FakeBroker(make_intraday())
    s = run_cycle(settings, broker, state, now=at("10:15:10"), dry_run=True)
    assert s["entries"] and broker.orders == [] and state.load_positions() == {}


def test_max_trades_per_day(rules, tmp_path):
    rules["risk"]["max_trades_per_day"] = 0
    settings = Settings(rules=rules, state_dir=tmp_path / "state", log_dir=tmp_path / "logs")
    state = State(settings.state_dir, settings.log_dir)
    state.write_watchlist([dict(symbol="XYZ", gap_pct=4.0, price=52.4)])
    broker = FakeBroker(make_intraday())
    assert run_cycle(settings, broker, state, now=at("10:15:10"))["entries"] == []
