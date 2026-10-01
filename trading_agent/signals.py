"""Turn indicators (+ fundamentals, news tone, macro regime) into entry/exit suggestions.

Rules, evaluated on the last completed daily bar:

Trend      uptrend   = close > SMA200 and SMA50 > SMA200
           downtrend = close < SMA200 and SMA50 < SMA200
Momentum   MACD histogram sign, plus fresh MACD/signal crossovers (last 3 bars)
RSI        40-65 healthy, >70 overbought, <30 oversold (a pullback buy in an uptrend)
Crosses    golden / death cross of SMA50 vs SMA200 in the last 10 bars

Entry  BUY            uptrend, MACD bullish, RSI < 70, score >= threshold
       BUY_PULLBACK   uptrend, RSI <= 40, close still above SMA200
Exit   SELL           close < SMA200, fresh death cross, close < chandelier stop,
                      or a bearish MACD cross while RSI > 70
Stops  initial stop   entry - 2 x ATR14
       trailing stop  22-day high - 3 x ATR14 (chandelier)
       target         entry + 2R  (R = entry - stop)
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import pandas as pd

from .indicators import compute_all, crossed_above, crossed_below


@dataclass
class Suggestion:
    symbol: str
    sector: str
    action: str  # BUY | BUY_PULLBACK | HOLD | ADD | SELL | AVOID | WATCH
    score: float
    close: float
    as_of: str
    sma50: float
    sma200: float
    rsi: float
    macd: float
    macd_signal: float
    macd_hist: float
    atr: float
    trend: str
    entry: float | None = None
    stop: float | None = None
    trailing_stop: float | None = None
    target: float | None = None
    risk_per_share: float | None = None
    shares: int | None = None
    position_value: float | None = None
    held_quantity: float = 0.0
    market_cap: float | None = None
    reasons: list[str] = field(default_factory=list)
    fundamentals: dict | None = None
    news: dict | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}


def _recent(flags: pd.Series, bars: int) -> bool:
    return bool(flags.tail(bars).any())


def technical_view(df: pd.DataFrame, cfg) -> dict:
    """Compute indicators and a technical score in roughly [-100, 100]."""
    if len(df) < cfg.sma_slow + 5:
        raise ValueError(f"need at least {cfg.sma_slow + 5} bars, got {len(df)}")
    ind = compute_all(df, cfg)
    last = ind.iloc[-1]
    close, fast, slow = float(last.close), float(last.sma_fast), float(last.sma_slow)
    rsi, hist, atr = float(last.rsi), float(last.macd_hist), float(last.atr)

    if close > slow and fast > slow:
        trend = "uptrend"
    elif close < slow and fast < slow:
        trend = "downtrend"
    else:
        trend = "mixed"

    golden = _recent(crossed_above(ind.sma_fast, ind.sma_slow), 10)
    death = _recent(crossed_below(ind.sma_fast, ind.sma_slow), 10)
    macd_bull = _recent(crossed_above(ind.macd, ind.macd_signal), 3)
    macd_bear = _recent(crossed_below(ind.macd, ind.macd_signal), 3)
    extension_atr = (close - fast) / atr if atr else 0.0

    score, reasons = 0.0, []

    def add(points: float, why: str) -> None:
        nonlocal score
        score += points
        reasons.append(f"{points:+.0f} {why}")

    add({"uptrend": 30, "downtrend": -30, "mixed": 0}[trend], f"{trend} (close vs 50/200-day SMA)")
    add(15 if hist > 0 else -15, f"MACD histogram {'positive' if hist > 0 else 'negative'} ({hist:+.2f})")
    if macd_bull:
        add(10, "fresh bullish MACD crossover")
    if macd_bear:
        add(-10, "fresh bearish MACD crossover")
    if golden:
        add(10, "golden cross (50 over 200) in last 10 bars")
    if death:
        add(-10, "death cross (50 under 200) in last 10 bars")
    if rsi > 70:
        add(-15, f"RSI overbought ({rsi:.0f})")
    elif rsi < 30:
        add(10 if trend == "uptrend" else -5, f"RSI oversold ({rsi:.0f})")
    elif 40 <= rsi <= 65:
        add(10, f"RSI healthy ({rsi:.0f})")
    if extension_atr > 3:
        add(-10, f"extended {extension_atr:.1f} ATR above 50-day SMA")

    return {
        "ind": ind,
        "close": close,
        "sma50": fast,
        "sma200": slow,
        "rsi": rsi,
        "macd": float(last.macd),
        "macd_signal": float(last.macd_signal),
        "macd_hist": hist,
        "atr": atr,
        "chandelier": float(last.chandelier_stop),
        "trend": trend,
        "golden": golden,
        "death": death,
        "macd_bull": macd_bull,
        "macd_bear": macd_bear,
        "score": score,
        "reasons": reasons,
        "as_of": str(ind.index[-1].date()),
    }


def fundamental_adjustment(f) -> tuple[float, list[str]]:
    if f is None:
        return 0.0, []
    pts, why = 0.0, []
    if f.revenue_growth is not None:
        g = f.revenue_growth * 100
        if g > 10:
            pts += 5; why.append(f"+5 revenue growth {g:.1f}% y/y (SEC XBRL)")
        elif g < 0:
            pts -= 5; why.append(f"-5 revenue shrinking {g:.1f}% y/y (SEC XBRL)")
    if f.profitable is False:
        pts -= 5; why.append("-5 net loss in latest fiscal year (SEC XBRL)")
    if any(x["form"] == "8-K" for x in f.recent_filings):
        why.append("note: 8-K filed in last 30 days - check for material events")
    return pts, why


def news_adjustment(news) -> tuple[float, list[str]]:
    if news is None or news.avg_tone is None:
        return 0.0, []
    if news.avg_tone >= 2:
        return 3.0, [f"+3 positive news tone {news.avg_tone:+.2f} (GDELT 7d)"]
    if news.avg_tone <= -2:
        return -5.0, [f"-5 negative news tone {news.avg_tone:+.2f} (GDELT 7d)"]
    return 0.0, []


def position_size(entry: float, stop: float, equity: float, cfg, multiplier: float = 1.0) -> int:
    risk_per_share = entry - stop
    if risk_per_share <= 0 or equity <= 0:
        return 0
    by_risk = equity * cfg.risk_per_trade * multiplier / risk_per_share
    by_cap = equity * cfg.max_position_pct * multiplier / entry
    return max(0, math.floor(min(by_risk, by_cap)))


def build_suggestion(
    symbol: str,
    sector: str,
    df: pd.DataFrame,
    cfg,
    *,
    equity: float,
    macro=None,
    fundamentals=None,
    news=None,
    holding=None,
) -> Suggestion:
    tv = technical_view(df, cfg)
    score = tv["score"]
    reasons = list(tv["reasons"])
    for pts, why in (fundamental_adjustment(fundamentals), news_adjustment(news)):
        score += pts
        reasons.extend(why)

    threshold = cfg.min_buy_score + (macro.buy_threshold_adjustment if macro else 0.0)
    size_mult = macro.size_multiplier if macro else 1.0
    close, atr = tv["close"], tv["atr"]
    held = holding.quantity if holding else 0.0

    # Trend breaks rule out new entries; stop/overbought exits only apply to open positions.
    trend_breaks = []
    if close < tv["sma200"]:
        trend_breaks.append("close below 200-day SMA")
    if tv["death"]:
        trend_breaks.append("recent death cross")
    exit_reasons = list(trend_breaks)
    if close < tv["chandelier"]:
        exit_reasons.append(f"close below ATR trailing stop {tv['chandelier']:.2f}")
    if tv["macd_bear"] and tv["rsi"] > 70:
        exit_reasons.append("bearish MACD cross while overbought")

    is_buy = tv["trend"] == "uptrend" and (tv["macd_hist"] > 0 or tv["macd_bull"]) and tv["rsi"] < 70 and score >= threshold
    is_pullback = tv["trend"] == "uptrend" and tv["rsi"] <= 40 and close >= tv["sma200"]

    if held > 0:
        action = "SELL" if exit_reasons else ("ADD" if is_buy or is_pullback else "HOLD")
    elif trend_breaks:
        action = "AVOID"
    elif is_pullback:
        action = "BUY_PULLBACK"
    elif is_buy:
        action = "BUY"
    elif tv["trend"] == "downtrend":
        action = "AVOID"
    else:
        action = "WATCH"

    for r in exit_reasons if held > 0 else trend_breaks:
        reasons.append(f"exit signal: {r}")
    if macro and macro.regime != "neutral":
        reasons.append(f"macro regime {macro.regime}: buy threshold {threshold:.0f}, size x{size_mult}")

    s = Suggestion(
        symbol=symbol,
        sector=sector,
        action=action,
        score=round(score, 1),
        close=close,
        as_of=tv["as_of"],
        sma50=tv["sma50"],
        sma200=tv["sma200"],
        rsi=tv["rsi"],
        macd=tv["macd"],
        macd_signal=tv["macd_signal"],
        macd_hist=tv["macd_hist"],
        atr=atr,
        trend=tv["trend"],
        trailing_stop=tv["chandelier"],
        held_quantity=held,
        reasons=reasons,
        fundamentals=asdict(fundamentals) if fundamentals else None,
        news=asdict(news) if news else None,
    )

    if action in ("BUY", "BUY_PULLBACK", "ADD"):
        s.entry = close
        s.stop = close - cfg.atr_stop_mult * atr
        s.risk_per_share = s.entry - s.stop
        s.target = s.entry + cfg.reward_risk * s.risk_per_share
        s.shares = position_size(s.entry, s.stop, equity, cfg, size_mult)
        s.position_value = round(s.shares * s.entry, 2)
    elif action in ("HOLD", "SELL") and holding:
        # Protect open positions: never loosen a stop below the original 2-ATR level.
        initial = holding.avg_cost - cfg.atr_stop_mult * atr
        s.stop = max(tv["chandelier"], initial)
    return s
