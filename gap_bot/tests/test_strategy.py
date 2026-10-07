import datetime as dt

import pandas as pd
import pytest

from gap_bot import strategy as st
from gap_bot.config import PaperOnlyError, Settings, check_paper_accounts, check_paper_only

from .conftest import DAY, make_daily, make_intraday


def et(hhmm, day=DAY):
    return pd.Timestamp(f"{day} {hhmm}", tz=st.ET)


@pytest.mark.parametrize("hhmm,expected", [
    ("09:00", "too_early"), ("09:45", "manage_only"), ("10:05", "ok"), ("15:30", "ok"),
    ("15:31", "manage_only"), ("15:51", "force_close"), ("16:00", "closed"),
])
def test_time_gate(rules, hhmm, expected):
    assert st.time_gate(et(hhmm), rules["time_gates_et"]) == expected


def test_time_gate_weekend(rules):
    assert st.time_gate(et("11:00", "2026-10-03"), rules["time_gates_et"]) == "weekend"


def _today_until(intraday, hhmm):
    return intraday[intraday.index < et(hhmm)]


def test_entry_passes_on_breakout(rules):
    ctx = st.daily_context(make_daily(), DAY)
    d = st.evaluate_entry(ctx, _today_until(make_intraday(), "10:15"), rules)
    assert d.passed, d.reason
    assert d.gap_pct == pytest.approx(4.0)
    assert d.low_of_day == pytest.approx(51.55)


def test_entry_fails_before_breaking_premarket_high(rules):
    ctx = st.daily_context(make_daily(), DAY)
    d = st.evaluate_entry(ctx, _today_until(make_intraday(), "10:10"), rules)
    assert not d.passed
    assert not d.checks["I1_above_premarket_high"]


def test_entry_fails_small_gap(rules):
    ctx = st.daily_context(make_daily(start_price=51.5), DAY)
    d = st.evaluate_entry(ctx, _today_until(make_intraday(), "10:15"), rules)
    assert not d.checks["D3_gap"]


def test_entry_fails_low_relative_volume(rules):
    ctx = st.daily_context(make_daily(volume=50_000_000), DAY)
    d = st.evaluate_entry(ctx, _today_until(make_intraday(), "10:15"), rules)
    assert not d.checks["I3_relative_volume"]


def test_entry_fails_below_sma(rules):
    daily = make_daily()
    daily["close"] = daily["close"].iloc[::-1].to_numpy()  # downtrend: last close is the lowest
    ctx = st.daily_context(daily, DAY)
    d = st.evaluate_entry(ctx, _today_until(make_intraday(prev_close=35.0), "10:15"), rules)
    assert not d.checks["D2_above_sma"]


def test_daily_context_needs_history():
    assert st.daily_context(make_daily(n=50), DAY) is None


def test_position_size_respects_all_caps(rules):
    # risk cap: 25k * 1% / 1.00 = 250 shares; 10% cap: 2500/50 = 50; trade cap 2500/50 = 50
    assert st.position_size(50.0, 49.0, 25_000, 2_500, rules) == 50
    # wide stop: risk cap binds -> 250 / 10 = 25 shares
    assert st.position_size(50.0, 40.0, 25_000, 2_500, rules) == 25
    assert st.position_size(50.0, 50.0, 25_000, 2_500, rules) == 0


def test_initial_stop(rules):
    assert st.initial_stop(100.0, rules) == 99.0


def test_swing_low():
    lows = [10, 9, 8, 7, 8, 9, 10, 9.5, 9.6]
    df = pd.DataFrame({"low": lows})
    assert st.latest_swing_low(df, 2) == 7


def test_manage_partial_then_breakeven(rules):
    pos = st.Position("XYZ", 30, 50.0, 48.0, 48.0, entry_time=str(et("10:10")))
    bars = make_intraday().loc[et("10:10"):et("10:10")].copy()
    bars["close"] = 51.6   # 0.8R
    actions = st.manage_position(pos, bars, rules)
    assert [a[0] for a in actions] == ["sell_partial"]
    assert actions[0][1] == 9
    st.apply_action(pos, actions[0])
    assert pos.qty == 21 and pos.partial_done

    bars["close"] = 52.1   # 1.05R
    actions = st.manage_position(pos, bars, rules)
    assert actions == [("move_stop", 50.0, "breakeven at 1.05R")]
    st.apply_action(pos, actions[0])
    assert pos.breakeven_done and pos.stop == 50.0


def test_paper_guard():
    check_paper_only(Settings(port=7497))
    check_paper_only(Settings(port=4002))
    for bad in (Settings(port=7496), Settings(port=4001), Settings(port=7497, paper_trading=False), Settings(port=1234)):
        with pytest.raises(PaperOnlyError):
            check_paper_only(bad)
    check_paper_accounts(["DU123"])
    with pytest.raises(PaperOnlyError):
        check_paper_accounts(["U123"])
    with pytest.raises(PaperOnlyError):
        check_paper_accounts([])
