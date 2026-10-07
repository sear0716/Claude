import pandas as pd

from gap_bot.backtest import BacktestConfig, run_backtest, save_results
from gap_bot.perf import summarize

from .conftest import DAY, make_daily, make_intraday


def test_backtest_gap_and_go_day(rules, tmp_path):
    data = {"XYZ": (make_daily(), make_intraday())}
    trades, days = run_backtest(data, rules, BacktestConfig())
    assert days[0]["watchlist"] == ["XYZ"]
    assert len(trades) == 1
    t = trades[0]
    # signal on the 10:10 bar's close, filled at the 10:15 bar's open
    assert t["entry_time"] == f"{DAY}T10:15:00-04:00"
    assert t["entry_price"] == 52.6
    assert t["initial_stop"] == round(51.55 * 0.99, 2)
    assert t["qty"] == 47                       # 2,500 trade cap / 52.6
    assert t["exit_reason"].startswith("sell_partial")
    assert t["exit_reason"].endswith("force_close")
    assert t["pnl"] > 0 and t["r"] > 1

    stats = summarize(trades, 25_000)
    assert stats["trades"] == 1 and stats["win_rate_pct"] == 100.0
    out = save_results(tmp_path, trades, stats, days)
    assert (out / "trades.csv").exists() and (out / "equity.csv").exists()


def test_backtest_stop_out(rules):
    # breakout then collapse through the low of day
    path = [51.9, 51.7, 51.6, 51.8, 52.0, 52.3, 52.35, 52.4, 52.6, 52.8, 52.0, 51.5, 50.8, 50.0]
    data = {"XYZ": (make_daily(), make_intraday(path=path))}
    trades, _ = run_backtest(data, rules)
    assert len(trades) == 1
    t = trades[0]
    assert t["exit_reason"] == "stop"
    assert t["exit_price"] <= t["initial_stop"]
    assert -1.3 < t["r"] < -0.9


def test_no_gap_no_trade(rules):
    data = {"XYZ": (make_daily(start_price=51.8), make_intraday())}
    trades, days = run_backtest(data, rules)
    assert trades == [] and days == []


def test_respects_max_concurrent_positions(rules):
    rules["risk"]["max_concurrent_positions"] = 2
    data = {s: (make_daily(), make_intraday()) for s in ["AAA", "BBB", "CCC"]}
    trades, _ = run_backtest(data, rules)
    assert len(trades) == 2
