"""One pass of the trading loop. Run it every 5 minutes during the session (see `run`)."""
import datetime as dt
import logging

import pandas as pd

from . import strategy as st
from .notify import notify

log = logging.getLogger(__name__)


def completed_today(bars, now):
    """Today's bars whose 5-minute window has fully closed by `now`."""
    now = pd.Timestamp(now).tz_convert(st.ET)
    ends = bars.index + pd.Timedelta(minutes=st.BAR_MINUTES)
    return bars[(bars.index.date == now.date()) & (ends <= now)]


def evaluate_symbol(broker, symbol, rules, now):
    e = rules["entry"]
    daily = broker.daily_bars(symbol)
    ctx = st.daily_context(daily, now.date(), e["D2_sma_length"], e["I3_volume_lookback_days"])
    bars = completed_today(broker.intraday_bars(symbol), now)
    return st.evaluate_entry(ctx, bars, rules), bars


def run_cycle(settings, broker, state, now=None, dry_run=False):
    rules = settings.rules
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(st.ET)
    gate = st.time_gate(now, rules["time_gates_et"])
    summary = dict(time=now.isoformat(timespec="minutes"), gate=gate, exits=[], entries=[], actions=[])
    if gate in ("weekend", "too_early", "closed"):
        state.log_decision(event="cycle_skipped", gate=gate)
        return summary

    def record(**row):
        state.record_trade(timestamp_iso=now.isoformat(timespec="seconds"), **row)

    positions = state.load_positions()
    held = broker.positions()

    # ---- manage open positions
    for sym, pos in list(positions.items()):
        stop_px = broker.stop_fill_price(pos.stop_order_id) if pos.stop_order_id else None
        if stop_px is not None:
            record(symbol=sym, side="SELL", size=pos.qty, fill_price=stop_px,
                               order_id=pos.stop_order_id, status="Filled", reason="stop",
                               initial_stop=pos.initial_stop)
            notify(settings, f"Stopped out {sym}", f"{pos.qty} @ {stop_px:.2f} ({pos.r_multiple(stop_px):+.2f}R)")
            summary["exits"].append((sym, "stop", stop_px))
            del positions[sym]
            continue
        if held.get(sym, 0) <= 0:
            state.log_decision(event="position_gone", symbol=sym, note="no broker position; dropping from state")
            summary["exits"].append((sym, "reconciled", None))
            del positions[sym]
            continue
        if gate == "force_close":
            if dry_run:
                summary["actions"].append((sym, "force_close (dry run)"))
                continue
            if pos.stop_order_id:
                broker.cancel(pos.stop_order_id)
            res = broker.market(sym, "SELL", pos.qty)
            record(symbol=sym, side="SELL", size=res["filled"], fill_price=res["fill_price"],
                               order_id=res["order_id"], status=res["status"], reason="force_close",
                               initial_stop=pos.initial_stop)
            notify(settings, f"Force-closed {sym}", f"{res['filled']} @ {res['fill_price']:.2f}")
            summary["exits"].append((sym, "force_close", res["fill_price"]))
            del positions[sym]
            continue

        bars = completed_today(broker.intraday_bars(sym), now)
        bars = bars[bars.index.time >= st._t(rules["time_gates_et"]["market_open"])]
        if bars.empty:
            continue
        for action in st.manage_position(pos, bars, rules):
            kind, value, reason = action
            summary["actions"].append((sym, kind, value, reason))
            state.log_decision(event=kind, symbol=sym, value=value, reason=reason, dry_run=dry_run)
            if dry_run:
                continue
            if kind == "sell_partial":
                res = broker.market(sym, "SELL", value)
                if res["filled"] <= 0:
                    continue
                action = (kind, res["filled"], reason)
                record(symbol=sym, side="SELL", size=res["filled"], fill_price=res["fill_price"],
                                   order_id=res["order_id"], status=res["status"], reason="partial",
                                   initial_stop=pos.initial_stop)
                broker.modify_stop(pos.stop_order_id, qty=pos.qty - res["filled"])
                notify(settings, f"Partial {sym}", f"sold {res['filled']} @ {res['fill_price']:.2f} ({reason})")
            elif kind == "move_stop":
                broker.modify_stop(pos.stop_order_id, stop_price=value)
                notify(settings, f"Stop moved {sym}", f"{pos.stop:.2f} -> {value:.2f} ({reason})")
            st.apply_action(pos, action)

    # ---- new entries
    if gate == "ok":
        r = rules["risk"]
        entries_today = state.entries_today(now.date())
        traded_today = state.symbols_entered_today(now.date())
        for sym in state.read_watchlist():
            if len(positions) >= r["max_concurrent_positions"] or entries_today >= r["max_trades_per_day"]:
                state.log_decision(event="entry_capped", positions=len(positions), entries_today=entries_today)
                break
            if sym in positions or sym in traded_today or held.get(sym, 0) != 0:
                continue
            try:
                decision, bars = evaluate_symbol(broker, sym, rules, now)
            except Exception as exc:  # noqa: BLE001 - one bad symbol shouldn't stop the cycle
                state.log_decision(event="evaluate_error", symbol=sym, error=str(exc))
                continue
            state.log_decision(event="evaluate", symbol=sym, passed=decision.passed,
                               reason=decision.reason, checks=decision.checks)
            if not decision.passed:
                continue
            stop = st.initial_stop(decision.low_of_day, rules)
            qty = st.position_size(decision.price, stop, settings.portfolio_value, settings.max_trade_size, rules)
            if qty < 1:
                state.log_decision(event="size_zero", symbol=sym, price=decision.price, stop=stop)
                continue
            if dry_run:
                summary["entries"].append((sym, qty, decision.price, stop, "dry run"))
                continue
            res = broker.market(sym, "BUY", qty)
            record(symbol=sym, side="BUY", size=res["filled"], fill_price=res["fill_price"],
                               order_id=res["order_id"], status=res["status"], reason=decision.reason,
                               initial_stop=stop)
            if res["filled"] <= 0:
                continue
            entries_today += 1
            stop_id = broker.place_stop(sym, res["filled"], stop)
            positions[sym] = st.Position(symbol=sym, qty=res["filled"], entry_price=res["fill_price"],
                                         initial_stop=stop, stop=stop, entry_time=bars.index[-1].isoformat(),
                                         stop_order_id=stop_id)
            notify(settings, f"Entered {sym}",
                   f"{res['filled']} @ {res['fill_price']:.2f}, stop {stop:.2f}, gap {decision.gap_pct:.1f}%")
            summary["entries"].append((sym, res["filled"], res["fill_price"], stop))

    state.save_positions(positions)
    state.log_decision(event="cycle_done", **{k: v for k, v in summary.items() if k != "time"})
    return summary
