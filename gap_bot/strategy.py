"""Pure strategy logic shared by the live cycle and the backtest.

Bars are pandas DataFrames indexed by the bar's START time (tz-aware, US/Eastern)
with columns open, high, low, close, volume. Nothing here talks to a broker.
"""
import datetime as dt
import math
from dataclasses import asdict, dataclass, field
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")
BAR_MINUTES = 5
SESSION_MINUTES = 390  # 09:30-16:00


def _t(hhmm):
    h, m = hhmm.split(":")
    return dt.time(int(h), int(m))


# ---------------------------------------------------------------- time gates

def time_gate(now, gates):
    """Return one of: weekend, too_early, manage_only, ok, force_close, closed."""
    now = now.astimezone(ET)
    if now.weekday() >= 5:
        return "weekend"
    t = now.time()
    if t < _t(gates["market_open"]):
        return "too_early"
    if t >= _t(gates["market_close"]):
        return "closed"
    if t >= _t(gates["force_close"]):
        return "force_close"
    if _t(gates["earliest_entry"]) <= t <= _t(gates["latest_entry"]):
        return "ok"
    return "manage_only"


# ---------------------------------------------------------------- entry rules

@dataclass
class DailyContext:
    prev_high: float
    prev_close: float
    sma: float           # SMA of closes up to and including the previous day
    avg_volume: float    # average daily volume over the lookback, excluding today


def daily_context(daily, today, sma_length=200, volume_lookback=14):
    """Summarise daily bars strictly before `today` (a date). None if history is too short."""
    hist = daily[daily.index.date < today] if len(daily) else daily
    if len(hist) < max(sma_length, volume_lookback):
        return None
    return DailyContext(
        prev_high=float(hist["high"].iloc[-1]),
        prev_close=float(hist["close"].iloc[-1]),
        sma=float(hist["close"].iloc[-sma_length:].mean()),
        avg_volume=float(hist["volume"].iloc[-volume_lookback:].mean()),
    )


def split_session(bars, gates):
    """(premarket bars, regular-session bars) for one day's intraday bars."""
    open_t, close_t = _t(gates["market_open"]), _t(gates["market_close"])
    times = bars.index.time
    pre = bars[times < open_t]
    rth = bars[(times >= open_t) & (times < close_t)]
    return pre, rth


@dataclass
class EntryDecision:
    passed: bool
    price: float = math.nan
    gap_pct: float = math.nan
    low_of_day: float = math.nan
    checks: dict = field(default_factory=dict)
    reason: str = ""


def evaluate_entry(ctx, bars_today, rules):
    """Apply D1-D3 and I1-I3 to one symbol using today's COMPLETED 5-min bars.

    D1 last price > previous day's high
    D2 previous close > N-day SMA
    D3 gap (today's open vs previous close) >= min gap %
    I1 last price > premarket high
    I2 last bar closes above the high of every earlier regular-session bar (new high of day)
    I3 relative volume >= threshold: today's session volume vs the average daily volume
       pro-rated to the fraction of the session that has elapsed
    """
    e, gates = rules["entry"], rules["time_gates_et"]
    if ctx is None:
        return EntryDecision(False, reason="not enough daily history")
    pre, rth = split_session(bars_today, gates)
    if len(rth) < 2:
        return EntryDecision(False, reason="need at least two regular-session bars")

    price = float(rth["close"].iloc[-1])
    day_open = float(rth["open"].iloc[0])
    gap_pct = (day_open / ctx.prev_close - 1) * 100
    lod = float(rth["low"].min())
    prior_hod = float(rth["high"].iloc[:-1].max())
    pm_high = float(pre["high"].max()) if len(pre) else math.nan

    open_dt = rth.index[0].replace(hour=_t(gates["market_open"]).hour, minute=_t(gates["market_open"]).minute)
    elapsed = (rth.index[-1] + pd.Timedelta(minutes=BAR_MINUTES) - open_dt).total_seconds() / 60
    expected = ctx.avg_volume * min(max(elapsed, BAR_MINUTES), SESSION_MINUTES) / SESSION_MINUTES
    rvol = float(rth["volume"].sum()) / expected if expected > 0 else 0.0

    checks = {
        "D1_above_prev_high": (price > ctx.prev_high) if e["D1_above_prev_high"] else True,
        "D2_above_sma": ctx.prev_close > ctx.sma,
        "D3_gap": gap_pct >= e["D3_min_gap_pct"],
        "I1_above_premarket_high": (not math.isnan(pm_high) and price > pm_high) if e["I1_above_premarket_high"] else True,
        "I2_new_high_of_day": (price > prior_hod) if e["I2_new_high_of_day"] else True,
        "I3_relative_volume": rvol >= e["I3_min_relative_volume"],
    }
    failed = [k for k, ok in checks.items() if not ok]
    detail = dict(price=round(price, 4), prev_high=ctx.prev_high, prev_close=ctx.prev_close,
                  sma=round(ctx.sma, 4), gap_pct=round(gap_pct, 2), premarket_high=pm_high,
                  prior_high_of_day=prior_hod, rvol=round(rvol, 2))
    return EntryDecision(
        passed=not failed, price=price, gap_pct=gap_pct, low_of_day=lod,
        checks={**checks, "detail": detail},
        reason="all rules passed" if not failed else "failed " + ", ".join(failed),
    )


# ---------------------------------------------------------------- sizing

def initial_stop(low_of_day, rules):
    return round(low_of_day * (1 - rules["exits"]["initial_stop_below_lod_pct"] / 100), 2)


def position_size(entry, stop, portfolio_value, max_trade_size, rules):
    """Shares so that a stop-out loses at most risk_per_trade_pct of the portfolio,
    capped by max_position_pct of the portfolio and MAX_TRADE_SIZE_USD."""
    r = rules["risk"]
    per_share_risk = entry - stop
    if per_share_risk <= 0 or entry <= 0:
        return 0
    by_risk = portfolio_value * r["risk_per_trade_pct"] / 100 / per_share_risk
    by_position = portfolio_value * r["max_position_pct"] / 100 / entry
    by_trade_cap = max_trade_size / entry
    return int(math.floor(min(by_risk, by_position, by_trade_cap)))


# ---------------------------------------------------------------- exits

@dataclass
class Position:
    symbol: str
    qty: int
    entry_price: float
    initial_stop: float
    stop: float
    entry_time: str
    partial_done: bool = False
    breakeven_done: bool = False
    stop_order_id: int | None = None

    @property
    def risk_per_share(self):
        return self.entry_price - self.initial_stop

    def r_multiple(self, price):
        return (price - self.entry_price) / self.risk_per_share if self.risk_per_share > 0 else 0.0

    def to_dict(self):
        return asdict(self)


def latest_swing_low(bars, n):
    """Low of the most recent bar whose low is below the lows of the n bars before AND after it."""
    lows = bars["low"].to_numpy()
    for i in range(len(lows) - n - 1, n - 1, -1):
        window = list(lows[i - n:i]) + list(lows[i + 1:i + n + 1])
        if lows[i] < min(window):
            return float(lows[i])
    return None


def manage_position(pos, bars_today, rules):
    """Decide what to do with an open long position after the latest completed bar.

    Returns a list of actions, each one of
      ("sell_partial", shares, reason)
      ("move_stop", new_stop, reason)
    The caller executes them (live: orders; backtest: simulated fills) and updates `pos`.
    """
    x = rules["exits"]
    price = float(bars_today["close"].iloc[-1])
    r_now = pos.r_multiple(price)
    actions = []

    if not pos.partial_done and r_now >= x["partial_at_r"]:
        shares = int(math.floor(pos.qty * x["partial_fraction"]))
        if shares >= 1 and shares < pos.qty:
            actions.append(("sell_partial", shares, f"{r_now:.2f}R >= {x['partial_at_r']}R"))

    new_stop = pos.stop
    if not pos.breakeven_done and r_now >= x["breakeven_at_r"]:
        new_stop = max(new_stop, pos.entry_price)
        actions.append(("move_stop", round(new_stop, 2), f"breakeven at {r_now:.2f}R"))

    if pos.breakeven_done or any(a[0] == "move_stop" for a in actions):
        since_entry = bars_today[bars_today.index >= pd.Timestamp(pos.entry_time)]
        swing = latest_swing_low(since_entry, x["trail_swing_bars"])
        if swing is not None:
            trail = round(swing - x["trail_offset"], 2)
            if trail > new_stop and trail < price:
                new_stop = trail
                actions = [a for a in actions if a[0] != "move_stop"]
                actions.append(("move_stop", trail, f"trail to swing low {swing:.2f} - {x['trail_offset']}"))
    return actions


def apply_action(pos, action):
    """Update position state after an action has been carried out."""
    kind, value, _ = action
    if kind == "sell_partial":
        pos.qty -= value
        pos.partial_done = True
    elif kind == "move_stop":
        if value >= pos.entry_price:
            pos.breakeven_done = True
        pos.stop = value
    return pos
