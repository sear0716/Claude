"""Command line: python -m gap_bot <command>

  prefilter            scan the S&P 500 for opening gaps -> state/watchlist.txt
  check SYMBOL         evaluate one symbol against the entry rules (never trades)
  cycle [--dry-run]    one pass: manage open positions, then look for entries
  run [--dry-run]      stay up for the session: prefilter + cycle every 5 minutes, exit after the close
  backtest             replay the rules on historical bars (yfinance, IBKR or CSV)
  report               performance stats + dashboard/index.html from state/trades.csv
"""
import argparse
import datetime as dt
import json
import logging
import time

from . import strategy as st
from .config import PKG_DIR, load_rules, load_settings

log = logging.getLogger("gap_bot")


def _state(settings):
    from .state import State
    return State(settings.state_dir, settings.log_dir)


def cmd_prefilter(args):
    from .prefilter import run_prefilter
    settings = load_settings()
    print(json.dumps(run_prefilter(_state(settings), settings.rules), indent=2))


def cmd_check(args):
    from .broker import Broker
    from .cycle import evaluate_symbol
    settings = load_settings()
    now = dt.datetime.now(st.ET)
    with Broker(settings) as b:
        decision, _ = evaluate_symbol(b, args.symbol.upper(), settings.rules, now)
    stop = st.initial_stop(decision.low_of_day, settings.rules) if decision.low_of_day == decision.low_of_day else None
    print(json.dumps(dict(symbol=args.symbol.upper(), gate=st.time_gate(now, settings.rules["time_gates_et"]),
                          passed=decision.passed, reason=decision.reason, checks=decision.checks,
                          stop=stop), indent=2, default=str))


def cmd_cycle(args):
    from .broker import Broker
    from .cycle import run_cycle
    settings = load_settings()
    with Broker(settings) as b:
        print(json.dumps(run_cycle(settings, b, _state(settings), dry_run=args.dry_run), indent=2, default=str))


def cmd_run(args):
    from .broker import Broker
    from .cycle import run_cycle
    from .notify import notify
    from .prefilter import run_prefilter
    settings = load_settings()
    state = _state(settings)
    last_prefilter = None
    broker = Broker(settings).connect()
    notify(settings, "Gap bot started", f"paper account, dry_run={args.dry_run}")
    try:
        while True:
            now = dt.datetime.now(st.ET)
            gate = st.time_gate(now, settings.rules["time_gates_et"])
            if gate in ("weekend", "closed"):
                log.info("market %s - exiting", gate)
                break
            if (dt.time(9, 55) <= now.time() <= dt.time(13, 0)
                    and (last_prefilter is None or now - last_prefilter >= dt.timedelta(minutes=30))):
                try:
                    log.info("prefilter: %s", run_prefilter(state, settings.rules))
                    last_prefilter = now
                except Exception as exc:  # noqa: BLE001
                    log.exception("prefilter failed")
                    notify(settings, "Prefilter failed", str(exc))
            if gate != "too_early":
                try:
                    if not broker.ib.isConnected():
                        broker.connect()
                    log.info("cycle: %s", run_cycle(settings, broker, state, dry_run=args.dry_run))
                except Exception as exc:  # noqa: BLE001
                    log.exception("cycle crashed")
                    notify(settings, "Cycle crashed", str(exc))
            # wake 10 s after the next 5-minute boundary so that bar is complete
            nxt = (now.replace(second=0, microsecond=0) + dt.timedelta(minutes=5 - now.minute % 5)
                   + dt.timedelta(seconds=10))
            broker.ib.sleep(max(1.0, (nxt - dt.datetime.now(st.ET)).total_seconds()))
    finally:
        broker.disconnect()


def cmd_backtest(args):
    from .backtest import BacktestConfig, load_csv_dir, load_ibkr, load_yfinance, run_backtest, save_results
    from .perf import summarize
    from .prefilter import load_universe
    rules = load_rules(args.rules)
    symbols = [s.upper() for s in args.symbols] if args.symbols else load_universe()
    if args.source == "csv":
        data = load_csv_dir(args.csv_dir, set(symbols) if args.symbols else None)
    elif args.source == "ibkr":
        from .broker import Broker
        settings = load_settings()
        with Broker(settings) as b:
            data = load_ibkr(b, symbols, args.ibkr_duration)
    else:
        data = load_yfinance(symbols)
    if not data:
        raise SystemExit("no data loaded")
    cfg = BacktestConfig(portfolio_value=args.portfolio, max_trade_size=args.max_trade,
                         commission_per_share=args.commission, slippage_per_share=args.slippage)
    trades, days = run_backtest(data, rules, cfg)
    stats = summarize(trades, cfg.portfolio_value)
    out = save_results(args.out, trades, stats, days)
    print(json.dumps(stats, indent=2))
    print(f"\n{len(trades)} trades over {len(days)} days with gap candidates. Results in {out}/")


def cmd_report(args):
    from .perf import daily_report
    settings = load_settings()
    stats = daily_report(_state(settings), settings, PKG_DIR / "dashboard")
    print(json.dumps(stats, indent=2))
    print(f"Dashboard: {PKG_DIR / 'dashboard' / 'index.html'}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="gap_bot", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prefilter").set_defaults(func=cmd_prefilter)
    c = sub.add_parser("check")
    c.add_argument("symbol")
    c.set_defaults(func=cmd_check)
    for name, fn in (("cycle", cmd_cycle), ("run", cmd_run)):
        c = sub.add_parser(name)
        c.add_argument("--dry-run", action="store_true", help="log decisions, place no orders")
        c.set_defaults(func=fn)
    b = sub.add_parser("backtest")
    b.add_argument("symbols", nargs="*", help="default: the whole S&P 500 list")
    b.add_argument("--source", choices=["yfinance", "ibkr", "csv"], default="yfinance")
    b.add_argument("--csv-dir", default="data")
    b.add_argument("--ibkr-duration", default="30 D", help="5-min history to request from IBKR")
    b.add_argument("--rules", default=None, help="alternate rules.json to test")
    b.add_argument("--portfolio", type=float, default=25_000)
    b.add_argument("--max-trade", type=float, default=2_500)
    b.add_argument("--commission", type=float, default=0.005, help="$ per share (min $1/order)")
    b.add_argument("--slippage", type=float, default=0.0, help="$ per share on every fill")
    b.add_argument("--out", default=str(PKG_DIR / "backtests" / time.strftime("%Y%m%d-%H%M%S")))
    b.set_defaults(func=cmd_backtest)
    sub.add_parser("report").set_defaults(func=cmd_report)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args.func(args)


if __name__ == "__main__":
    main()
