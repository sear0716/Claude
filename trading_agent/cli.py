"""Command line entry point: `python -m trading_agent --help`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .agent import TradingAgent
from .config import Config
from .http import HttpClient
from .report import to_csv, to_markdown
from .sources import CSVSource, DemoSource, FredSource, GdeltSource, IBKRSource, SecEdgarSource
from .universe import load_universe


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="trading_agent",
        description="Scan US large caps across all sectors and suggest entries/exits "
        "from IBKR prices + SEC EDGAR + FRED + GDELT.",
    )
    src = p.add_argument_group("price source (default: Interactive Brokers TWS/Gateway)")
    src.add_argument("--prices-dir", help="read <SYMBOL>.csv files instead of IBKR")
    src.add_argument("--demo", action="store_true", help="synthetic prices, no external calls at all")
    p.add_argument("--symbols", help="comma-separated symbols to scan instead of the sector universe")
    p.add_argument("--universe-file", help='JSON {"Sector": ["SYM", ...]} replacing the built-in universe')
    p.add_argument("--top", type=int, default=10, help="number of entry candidates to report (default 10)")
    p.add_argument("--per-sector", type=int, default=10, help="largest N stocks kept per sector (default 10)")
    p.add_argument("--max-per-sector", type=int, default=3, help="cap on top picks from one sector (default 3)")
    p.add_argument("--equity", type=float, help="account equity for sizing when not using IBKR")
    p.add_argument("--no-sec", action="store_true", help="skip SEC EDGAR fundamentals")
    p.add_argument("--no-fred", action="store_true", help="skip FRED macro regime")
    p.add_argument("--no-news", action="store_true", help="skip GDELT news (it is rate limited to 1 req/5s)")
    p.add_argument("--briefing", action="store_true", help="add a Claude-written briefing (needs Anthropic credentials)")
    p.add_argument("--out", default="reports", help="directory for report.json / report.md / signals.csv")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def build_agent(args, cfg: Config) -> tuple[TradingAgent, IBKRSource | None]:
    if args.symbols:
        universe = {"Custom": [s.strip().upper() for s in args.symbols.split(",") if s.strip()]}
    else:
        universe = load_universe(args.universe_file)

    broker = None
    if args.demo:
        prices = DemoSource()
    elif args.prices_dir:
        prices = CSVSource(args.prices_dir)
    else:
        broker = IBKRSource(cfg)
        broker.connect()
        prices = broker

    online = not args.demo
    sec_http = HttpClient(cfg.sec_user_agent, cfg.cache_dir, {"www.sec.gov": 0.12, "data.sec.gov": 0.12})
    web_http = HttpClient(
        "trading-agent/1.0", cfg.cache_dir, {"api.gdeltproject.org": 5.5, "fred.stlouisfed.org": 0.5}
    )
    agent = TradingAgent(
        cfg,
        prices,
        broker=broker,
        sec=SecEdgarSource(sec_http) if online and not args.no_sec else None,
        fred=FredSource(web_http, cfg.fred_api_key) if online and not args.no_fred else None,
        gdelt=GdeltSource(web_http) if online and not args.no_news else None,
        universe=universe,
    )
    return agent, broker


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    cfg = Config()
    cfg.top_n = args.top
    cfg.per_sector = args.per_sector
    cfg.max_per_sector_in_top = args.max_per_sector
    if args.equity:
        cfg.default_equity = args.equity

    agent, broker = build_agent(args, cfg)
    try:
        report = agent.run(with_news=not args.no_news).to_dict()
    finally:
        if broker:
            broker.close()

    markdown = to_markdown(report)
    if args.briefing:
        from .briefing import write_briefing

        try:
            markdown = "# Briefing\n\n" + write_briefing(report) + "\n\n---\n\n" + markdown
        except Exception as exc:
            print(f"warning: briefing failed: {exc}", file=sys.stderr)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (out / "report.md").write_text(markdown)
    to_csv(report, out / "signals.csv")
    print(markdown)
    print(f"\nSaved {out / 'report.md'}, {out / 'report.json'}, {out / 'signals.csv'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
