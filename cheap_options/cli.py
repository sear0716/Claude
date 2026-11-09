"""Command line: python -m cheap_options AAPL MSFT NVDA"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import demo, providers, report
from .analysis import Config, Contract, Report, analyze
from .models import Snapshot
from .store import DEFAULT_PATH, IvStore


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cheap_options", description="Rank listed stock options by how cheaply they are priced.")
    p.add_argument("symbols", nargs="*", help="tickers to scan, e.g. AAPL MSFT SPY")
    p.add_argument("--watchlist", type=Path, help="text file with one ticker per line")
    p.add_argument("--provider", choices=["yahoo", "schwab"], default="yahoo", help="data source (default yahoo)")
    p.add_argument("--demo", action="store_true", help="run on built-in synthetic data, no network needed")
    p.add_argument("--from-snapshot", type=Path, nargs="+", metavar="FILE", help="analyse saved snapshot files instead of fetching")
    p.add_argument("--save-snapshot", type=Path, metavar="DIR", help="save the fetched data as SYMBOL_DATE.json in DIR")
    p.add_argument("--top", type=int, default=25, help="contracts to show (default 25)")
    p.add_argument("--sort", choices=["score", "vrp", "hist", "smile", "term"], default="score",
                   help="rank by the overall score (default) or by one measure, e.g. smile for strikes cheap vs their neighbours")
    p.add_argument("--min-score", type=float, default=0.0, help="hide contracts scoring below this")
    p.add_argument("--right", choices=["C", "P"], help="only calls (C) or only puts (P)")
    p.add_argument("--min-dte", type=int, default=7)
    p.add_argument("--max-dte", type=int, default=120)
    p.add_argument("--min-oi", type=int, default=100, help="minimum open interest (default 100)")
    p.add_argument("--min-volume", type=int, default=0)
    p.add_argument("--max-spread", type=float, default=0.25, help="maximum bid-ask spread as a fraction of mid (default 0.25)")
    p.add_argument("--min-delta", type=float, default=0.10, help="minimum absolute delta (default 0.10)")
    p.add_argument("--max-delta", type=float, default=0.60, help="maximum absolute delta (default 0.60)")
    p.add_argument("--rate", type=float, default=0.04, help="risk-free rate, e.g. 0.04 (default)")
    p.add_argument("--csv", type=Path, help="write the ranked contracts to a CSV file")
    p.add_argument("--json", type=Path, help="write the full result to a JSON file")
    p.add_argument("--iv-db", type=Path, default=DEFAULT_PATH, help=f"IV history database (default {DEFAULT_PATH})")
    p.add_argument("--no-store", action="store_true", help="do not read or record IV history")
    return p


def load_snapshots(args) -> list[Snapshot]:
    if args.demo:
        return demo.demo_snapshots()
    if args.from_snapshot:
        return [Snapshot.from_dict(json.loads(f.read_text())) for f in args.from_snapshot]
    symbols = list(args.symbols)
    if args.watchlist:
        symbols += [s.strip() for s in args.watchlist.read_text().splitlines() if s.strip() and not s.startswith("#")]
    symbols = list(dict.fromkeys(s.upper() for s in symbols))
    if not symbols:
        raise SystemExit("give at least one ticker, or use --demo")
    snaps = []
    for sym in symbols:
        try:
            snap = providers.fetch(args.provider, sym, args.max_dte)
        except Exception as exc:
            print(f"{sym}: skipped - {exc}", file=sys.stderr)
            continue
        if args.save_snapshot:
            args.save_snapshot.mkdir(parents=True, exist_ok=True)
            (args.save_snapshot / f"{sym}_{snap.asof}.json").write_text(json.dumps(snap.to_dict()))
        snaps.append(snap)
    return snaps


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config(rate=args.rate, min_dte=args.min_dte, max_dte=args.max_dte, min_open_interest=args.min_oi,
                 min_volume=args.min_volume, max_spread_pct=args.max_spread, min_abs_delta=args.min_delta,
                 max_abs_delta=args.max_delta)
    snaps = load_snapshots(args)
    if not snaps:
        print("no data fetched", file=sys.stderr)
        return 1
    # Demo data is synthetic and must never be mixed into the real IV history.
    store = None if (args.no_store or args.demo) else IvStore(args.iv_db)
    reports: list[Report] = []
    for snap in snaps:
        history = store.history(snap.symbol, snap.asof) if store else None
        rep = analyze(snap, cfg, history)
        if not rep.expiries:
            rep.notes.append("no usable option quotes in the chosen expiry range")
        if store and rep.iv30:
            store.record(snap.symbol, snap.asof, rep.iv30)
        reports.append(rep)
    if store:
        store.close()
    ranked: list[tuple[str, Contract]] = [
        (r.symbol, c) for r in reports for c in r.contracts
        if c.score >= args.min_score and (not args.right or c.quote.right == args.right)]
    if args.sort == "score":
        ranked.sort(key=lambda sc: sc[1].score, reverse=True)
    else:
        ranked.sort(key=lambda sc: (sc[1].components.get(args.sort, -2.0), sc[1].score), reverse=True)
    ranked = ranked[:args.top]
    print(report.render(reports, ranked))
    if args.csv:
        report.write_csv(args.csv, ranked)
    if args.json:
        report.write_json(args.json, reports, ranked)
    return 0
