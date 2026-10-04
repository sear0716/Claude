import numpy as np
import pytest

from trading_agent.config import Config
from trading_agent.signals import build_suggestion, position_size, technical_view
from trading_agent.sources.fred import MacroSnapshot
from trading_agent.sources.gdelt import NewsSignal
from trading_agent.sources.ibkr import Holding
from trading_agent.sources.sec_edgar import Fundamentals

from .conftest import make_ohlc, wavy_trend

CFG = Config()


def test_requires_enough_history():
    with pytest.raises(ValueError):
        technical_view(make_ohlc(np.linspace(1, 2, 100)), CFG)


def test_uptrend_classification(uptrend_df):
    tv = technical_view(uptrend_df, CFG)
    assert tv["trend"] == "uptrend"
    assert tv["sma50"] > tv["sma200"]
    assert any("uptrend" in r for r in tv["reasons"])


def test_downtrend_is_avoided_and_held_position_is_sold(downtrend_df):
    s = build_suggestion("XYZ", "Tech", downtrend_df, CFG, equity=100_000)
    assert s.trend == "downtrend"
    assert s.action == "AVOID"
    assert s.entry is None

    held = Holding("XYZ", 50, 150.0, "U1")
    s = build_suggestion("XYZ", "Tech", downtrend_df, CFG, equity=100_000, holding=held)
    assert s.action == "SELL"
    assert any("200-day" in r for r in s.reasons)


def _find_buy_df():
    # Slide the end of a wavy uptrend until a bar meets every BUY rule (MACD up, RSI < 70).
    closes = wavy_trend(100, 180, n=400, amp=0.05, period=80)
    for end in range(300, 400):
        df = make_ohlc(closes[:end])
        s = build_suggestion("ABC", "Tech", df, CFG, equity=100_000)
        if s.action == "BUY":
            return df, s
    pytest.fail("no BUY bar found in synthetic uptrend")


def test_buy_levels_and_sizing():
    _, s = _find_buy_df()
    assert s.stop == pytest.approx(s.entry - CFG.atr_stop_mult * s.atr)
    assert s.target == pytest.approx(s.entry + CFG.reward_risk * (s.entry - s.stop))
    risk_dollars = s.shares * (s.entry - s.stop)
    assert risk_dollars <= 100_000 * CFG.risk_per_trade + 1e-6
    assert s.position_value <= 100_000 * CFG.max_position_pct + 1e-6


def test_pullback_entry():
    closes = list(np.linspace(100, 200, 300))
    closes += list(np.linspace(200, 186, 8))  # sharp dip, still far above the 200-day SMA
    s = build_suggestion("PB", "Tech", make_ohlc(closes), CFG, equity=100_000)
    assert s.trend == "uptrend"
    assert s.rsi <= 40
    assert s.action == "BUY_PULLBACK"
    assert s.stop < s.entry


def test_risk_off_regime_halves_size_and_raises_bar():
    df, base = _find_buy_df()
    macro = MacroSnapshot(score=-3, regime="risk_off")
    s = build_suggestion("ABC", "Tech", df, CFG, equity=100_000, macro=macro)
    if s.action == "BUY":
        assert s.shares <= base.shares // 2 + 1
    else:
        assert base.score < CFG.min_buy_score + macro.buy_threshold_adjustment


def test_fundamentals_and_news_adjust_score():
    df, base = _find_buy_df()
    f = Fundamentals("ABC", revenue_growth=-0.10, net_income=-5.0)
    n = NewsSignal("q", avg_tone=-3.5)
    s = build_suggestion("ABC", "Tech", df, CFG, equity=100_000, fundamentals=f, news=n)
    assert s.score == pytest.approx(base.score - 15)
    assert any("SEC XBRL" in r for r in s.reasons)
    assert any("GDELT" in r for r in s.reasons)


def test_held_position_stop_never_below_initial_atr_stop(uptrend_df):
    held = Holding("ABC", 10, avg_cost=float(uptrend_df.close.iloc[-1]) * 1.5, account="U1")
    s = build_suggestion("ABC", "Tech", uptrend_df, CFG, equity=100_000, holding=held)
    if s.action in ("HOLD", "SELL"):
        assert s.stop >= held.avg_cost - CFG.atr_stop_mult * s.atr - 1e-9


def test_position_size_math():
    # $100k * 1% = $1,000 risk; $5 risk per share -> 200 shares, capped by 10% = $10k / $100 = 100.
    assert position_size(100, 95, 100_000, CFG) == 100
    assert position_size(100, 80, 100_000, CFG) == 50
    assert position_size(100, 100, 100_000, CFG) == 0
