"""python -m options_scout --help"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from .ivstore import IVStore
from .rates import risk_free_rate
from .report import to_markdown, write_reports
from .scanner import Scanner, ScanParams, top_cheap

DEFAULT_SYMBOLS = "SPY,QQQ,AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,JPM"


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="options_scout",
        description="Find stock options whose implied volatility is cheap versus the stock's realized "
        "volatility, its own 1-year IV range and neighbouring strikes. Read-only: never places orders.",
    )
    p.add_argument("symbols", nargs="?", default=DEFAULT_SYMBOLS, help=f"comma-separated (default {DEFAULT_SYMBOLS})")
    p.add_argument("--source", choices=["ibkr", "yahoo", "demo"], default="ibkr",
                   help="ibkr = TWS/Gateway (default); yahoo = free, delayed, needs `pip install yfinance`; "
                   "demo = synthetic offline data")
    p.add_argument("--fallback", choices=["yahoo", "none"], default="yahoo",
                   help="if IBKR is unreachable, use this instead (default yahoo)")
    p.add_argument("--min-dte", type=int, default=7)
    p.add_argument("--max-dte", type=int, default=120)
    p.add_argument("--moneyness", type=float, default=0.25, help="strikes within +/- this fraction of spot (0.25)")
    p.add_argument("--min-oi", type=float, default=100, help="minimum open interest (100)")
    p.add_argument("--min-volume", type=float, default=0)
    p.add_argument("--max-spread", type=float, default=0.15, help="max (ask-bid)/mid (0.15)")
    p.add_argument("--delta", default="0.10,0.80", help="abs delta range (0.10,0.80)")
    p.add_argument("--rate", type=float, help="risk-free rate as a decimal (default: 3-month T-bill from FRED)")
    p.add_argument("--div-yield", type=float, help="dividend yield for every symbol (default: from source, else 0)")
    p.add_argument("--half-life", type=float, default=30, help="vol mean-reversion half-life in trading days (30)")
    p.add_argument("--top", type=int, default=25)
    p.add_argument("--all", action="store_true", help="rank all liquid contracts, not only IV(ask) < forecast RV")
    p.add_argument("--out", default="reports/options", help="directory for the .md / .csv / .json reports")
    p.add_argument("--no-iv-store", action="store_true", help="don't log daily ATM IV for IV rank")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def make_source(args):
    if args.source == "demo":
        from .sources import DemoSource

        return DemoSource(rate=args.rate or 0.04), "demo (synthetic - not market data)"
    if args.source == "yahoo":
        from .sources import YahooSource

        return YahooSource(), "Yahoo Finance (delayed)"
    from .sources import IBKROptionsSource

    src = IBKROptionsSource()
    try:
        src.connect()
        return src, f"Interactive Brokers {src.cfg.ib_host}:{src.cfg.ib_port}"
    except (ConnectionError, RuntimeError) as exc:
        if args.fallback == "none":
            raise
        logging.warning("IBKR unavailable (%s). Falling back to Yahoo.", exc)
        from .sources import YahooSource

        return YahooSource(), "Yahoo Finance (delayed; IBKR fallback)"


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    lo_d, hi_d = (float(x) for x in args.delta.split(","))
    rate = args.rate if args.rate is not None else (0.04 if args.source == "demo" else risk_free_rate())
    params = ScanParams(
        min_dte=args.min_dte, max_dte=args.max_dte, lo_moneyness=1 - args.moneyness, hi_moneyness=1 + args.moneyness,
        min_open_interest=args.min_oi, min_volume=args.min_volume, max_spread_pct=args.max_spread,
        min_abs_delta=lo_d, max_abs_delta=hi_d, rate=rate, div_yield=args.div_yield, half_life_days=args.half_life,
    )
    source, label = make_source(args)
    store = None if (args.no_iv_store or args.source == "demo") else IVStore()
    try:
        summaries, contracts = Scanner(source, params, store).scan(symbols)
    finally:
        source.close()
    top = top_cheap(contracts, args.top, only_cheap=not args.all)
    meta = {"source": label, "rate": rate, "min_dte": args.min_dte, "max_dte": args.max_dte,
            "generated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"), "symbols": symbols}
    paths = write_reports(Path(args.out), summaries, contracts, top, meta)
    print(to_markdown(summaries, top, meta))
    print("Reports: " + ", ".join(str(p) for p in paths.values()), file=sys.stderr)
    return 0
