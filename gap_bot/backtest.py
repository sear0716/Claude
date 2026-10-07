"""Bar-by-bar backtest of the same rules the live cycle uses (strategy.py).

Fill model (no look-ahead):
  * every decision is made at the close of a completed 5-minute bar, like the live cycle;
  * market orders from that decision (entries, partial sells, force-close) fill at the NEXT bar's open;
  * a resting stop fills when a bar's low touches it, at the stop price or the bar's open if it gapped through;
  * the daily watchlist uses the opening gap (09:30 open vs prior close), not later prices.
Commission is IBKR fixed pricing by default ($0.005/share, $1 minimum per order).
"""
import csv
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import strategy as st

log = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    portfolio_value: float = 25_000.0
    max_trade_size: float = 2_500.0
    commission_per_share: float = 0.005
    commission_min: float = 1.0
    slippage_per_share: float = 0.0


@dataclass
class _Trade:
    symbol: str
    day: str
    entry_time: pd.Timestamp
    entry_price: float
    qty: int
    initial_stop: float
    pos: st.Position
    proceeds: float = 0.0
    sold: int = 0
    commission: float = 0.0
    exits: list = field(default_factory=list)


def _commission(cfg, qty):
    return max(cfg.commission_min, qty * cfg.commission_per_share) if qty else 0.0


def watchlist_for_day(day, data, rules):
    """Opening-gap scan for one day: [(symbol, gap_pct)] largest first."""
    u, gates = rules["universe"], rules["time_gates_et"]
    rows = []
    for sym, (daily, intraday) in data.items():
        hist = daily[daily.index.date < day]
        bars = intraday[intraday.index.date == day]
        _, rth = st.split_session(bars, gates)
        if hist.empty or rth.empty:
            continue
        open_px = float(rth["open"].iloc[0])
        gap = (open_px / float(hist["close"].iloc[-1]) - 1) * 100
        if gap >= u["min_gap_pct"] and open_px >= u["min_price"]:
            rows.append((sym, gap))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows[: u["max_watchlist"]]


def run_backtest(data, rules, cfg=None):
    """data: {symbol: (daily_df, intraday_5min_df)} with ET-indexed bars (see strategy.py).
    Returns (closed trades as dicts, per-day log)."""
    cfg = cfg or BacktestConfig()
    e, r, gates = rules["entry"], rules["risk"], rules["time_gates_et"]
    days = sorted({d for _, (_, intra) in data.items() for d in intra.index.date})
    trades, day_log = [], []

    for day in days:
        cands = watchlist_for_day(day, data, rules)
        if not cands:
            continue
        ctxs, today = {}, {}
        for sym, _ in cands:
            daily, intra = data[sym]
            ctxs[sym] = st.daily_context(daily, day, e["D2_sma_length"], e["I3_volume_lookback_days"])
            today[sym] = intra[intra.index.date == day]
        _, any_rth = st.split_session(pd.concat(today.values()).sort_index(), gates)
        starts = sorted(set(any_rth.index))

        open_trades: dict[str, _Trade] = {}
        pending = []          # (kind, symbol, qty, extra) executed at the next bar's open
        entries_today = 0
        traded_today = set()  # one entry per symbol per day, same as the live cycle
        for s in starts:
            # 1) pending market orders fill at this bar's open
            for kind, sym, qty, extra in pending:
                bar = today[sym].loc[s] if s in today[sym].index else None
                if bar is None:
                    continue
                if kind == "buy":
                    px = float(bar["open"]) + cfg.slippage_per_share
                    stop = extra["stop"]
                    if px <= stop:
                        continue
                    pos = st.Position(symbol=sym, qty=qty, entry_price=px, initial_stop=stop, stop=stop,
                                      entry_time=extra["signal_bar"].isoformat())
                    traded_today.add(sym)
                    open_trades[sym] = _Trade(sym, str(day), s, px, qty, stop, pos, commission=_commission(cfg, qty))
                    entries_today += 1
                elif sym in open_trades:
                    t = open_trades[sym]
                    qty = min(qty, t.pos.qty)
                    _sell(t, qty, float(bar["open"]) - cfg.slippage_per_share, kind, cfg)
                    if kind == "sell_partial":
                        st.apply_action(t.pos, ("sell_partial", qty, ""))
                    else:
                        t.pos.qty -= qty
                    if t.pos.qty <= 0:
                        trades.append(_finish(t, s))
                        del open_trades[sym]
            pending = []

            # 2) resting stops during this bar
            for sym, t in list(open_trades.items()):
                if s not in today[sym].index:
                    continue
                bar = today[sym].loc[s]
                if float(bar["low"]) <= t.pos.stop:
                    px = min(float(bar["open"]), t.pos.stop) - cfg.slippage_per_share
                    _sell(t, t.pos.qty, px, "stop", cfg)
                    t.pos.qty = 0
                    trades.append(_finish(t, s))
                    del open_trades[sym]

            # 3) decisions at this bar's close
            end = s + pd.Timedelta(minutes=st.BAR_MINUTES)
            gate = st.time_gate(end, gates)
            for sym, t in open_trades.items():
                if gate in ("force_close", "closed"):
                    pending.append(("force_close", sym, t.pos.qty, None))
                    continue
                bars = today[sym][(today[sym].index <= s) & (today[sym].index.time >= st._t(gates["market_open"]))]
                for action in st.manage_position(t.pos, bars, rules):
                    if action[0] == "sell_partial":
                        pending.append(("sell_partial", sym, action[1], None))
                    else:
                        st.apply_action(t.pos, action)

            if gate == "ok":
                for sym, _ in cands:
                    n_open = len(open_trades) + sum(1 for p in pending if p[0] == "buy")
                    n_entries = entries_today + sum(1 for p in pending if p[0] == "buy")
                    if n_open >= r["max_concurrent_positions"] or n_entries >= r["max_trades_per_day"]:
                        break
                    if sym in open_trades or sym in traded_today or any(p[1] == sym for p in pending):
                        continue
                    decision = st.evaluate_entry(ctxs[sym], today[sym][today[sym].index <= s], rules)
                    if not decision.passed:
                        continue
                    stop = st.initial_stop(decision.low_of_day, rules)
                    qty = st.position_size(decision.price, stop, cfg.portfolio_value, cfg.max_trade_size, rules)
                    if qty >= 1:
                        pending.append(("buy", sym, qty, dict(stop=stop, signal_bar=s)))

        # anything still open after the last bar closes at that bar's close
        for sym, t in open_trades.items():
            last = today[sym].iloc[-1]
            _sell(t, t.pos.qty, float(last["close"]), "end_of_data", cfg)
            trades.append(_finish(t, today[sym].index[-1]))
        day_log.append(dict(day=str(day), watchlist=[c[0] for c in cands],
                            trades=sum(1 for t in trades if t["day"] == str(day))))
    return trades, day_log


def _sell(t, qty, px, reason, cfg):
    t.proceeds += qty * px
    t.sold += qty
    t.commission += _commission(cfg, qty)
    t.exits.append(reason)


def _finish(t, when):
    exit_price = t.proceeds / t.sold
    gross = t.proceeds - t.qty * t.entry_price
    risk = t.entry_price - t.initial_stop
    return dict(symbol=t.symbol, day=t.day, entry_time=t.entry_time.isoformat(), exit_time=pd.Timestamp(when).isoformat(),
                qty=t.qty, entry_price=round(t.entry_price, 4), initial_stop=t.initial_stop,
                exit_price=round(exit_price, 4), commission=round(t.commission, 2),
                pnl=round(gross - t.commission, 2),
                r=round((exit_price - t.entry_price) / risk, 3) if risk > 0 else 0.0,
                exit_reason="+".join(t.exits))


# ------------------------------------------------------------------ data loading

def _normalise(df):
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].astype(float).dropna()
    idx = pd.DatetimeIndex(df.index)
    df.index = idx.tz_localize(st.ET) if idx.tz is None else idx.tz_convert(st.ET)
    return df


def load_yfinance(symbols, intraday_period="60d", daily_period="2y"):
    """Yahoo serves only ~60 days of 5-minute history; prepost=True includes premarket bars."""
    import yfinance as yf

    from .prefilter import yahoo_symbol

    out = {}
    for sym in symbols:
        tk = yf.Ticker(yahoo_symbol(sym))
        daily = tk.history(period=daily_period, interval="1d", auto_adjust=False)
        intra = tk.history(period=intraday_period, interval="5m", prepost=True, auto_adjust=False)
        if daily.empty or intra.empty:
            log.warning("no data for %s", sym)
            continue
        daily = _normalise(daily)
        daily.index = pd.DatetimeIndex([pd.Timestamp(d.date(), tz=st.ET) for d in daily.index])
        out[sym] = (daily, _normalise(intra))
    return out


def load_ibkr(broker, symbols, intraday_duration="30 D"):
    out = {}
    for sym in symbols:
        try:
            out[sym] = (broker.daily_bars(sym, "2 Y"), broker.intraday_bars(sym, intraday_duration))
        except Exception as exc:  # noqa: BLE001
            log.warning("skipping %s: %s", sym, exc)
    return out


def load_csv_dir(path, symbols=None):
    """Reads <SYM>_daily.csv and <SYM>_5min.csv (columns date,open,high,low,close,volume)."""
    path = Path(path)
    out = {}
    for f in sorted(path.glob("*_5min.csv")):
        sym = f.name[: -len("_5min.csv")]
        if symbols and sym not in symbols:
            continue
        daily_f = path / f"{sym}_daily.csv"
        if not daily_f.exists():
            continue
        intra = pd.read_csv(f, index_col="date")
        intra.index = pd.to_datetime(intra.index, utc=True)
        daily = pd.read_csv(daily_f, index_col="date")
        daily.index = pd.to_datetime(daily.index)
        out[sym] = (_normalise(daily), _normalise(intra))
    return out


def save_results(out_dir, trades, stats, day_log):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if trades:
        with open(out_dir / "trades.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(trades[0]))
            w.writeheader()
            w.writerows(trades)
    (out_dir / "summary.json").write_text(json.dumps(dict(stats=stats, days=day_log), indent=2, default=str))
    equity, rows = 0.0, []
    for t in trades:
        equity += t["pnl"]
        rows.append(dict(exit_time=t["exit_time"], symbol=t["symbol"], pnl=t["pnl"], equity=round(equity, 2)))
    if rows:
        with open(out_dir / "equity.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return out_dir
