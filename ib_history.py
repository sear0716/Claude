"""Read-only historical bar pull from TWS into CSV files.

Places no orders. Connects with readonly=True so the API session cannot submit orders.
Writes one CSV per symbol (date,open,high,low,close,volume,average,bar_count) to --out-dir.

Usage:  python ib_history.py AAPL [MSFT ...] [--duration "1 Y"] [--bar-size "1 day"]
        [--what TRADES] [--out-dir data] [--host 127.0.0.1] [--port 7497] [--client-id 18]
"""
import argparse
import csv
import re
from pathlib import Path

from ib_async import IB, Stock

COLUMNS = ["date", "open", "high", "low", "close", "volume", "average", "bar_count"]
WHAT_TO_SHOW = ["TRADES", "ADJUSTED_LAST", "MIDPOINT", "BID", "ASK", "BID_ASK"]


def ib_symbol(symbol: str) -> str:
    """IB writes share classes with a space: BRK.B / BRK-B -> 'BRK B'."""
    return symbol.upper().replace(".", " ").replace("-", " ")


def csv_path(out_dir: Path, symbol: str, bar_size: str, duration: str) -> Path:
    slug = lambda s: re.sub(r"[^A-Za-z0-9]+", "", s)
    return out_dir / f"{slug(symbol.upper())}_{slug(bar_size)}_{slug(duration)}.csv"


def write_bars(path: Path, bars) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for b in bars:
            w.writerow([b.date.isoformat(), b.open, b.high, b.low, b.close,
                        b.volume, b.average, b.barCount])


def pull(ib, symbol, args):
    contract = Stock(ib_symbol(symbol), args.exchange, args.currency)
    qualified = ib.qualifyContracts(contract)
    if not qualified or not getattr(qualified[0], "conId", 0):
        raise LookupError(f"IB could not find a {args.currency} stock for {symbol}")
    return ib.reqHistoricalData(
        qualified[0],
        endDateTime="",
        durationStr=args.duration,
        barSizeSetting=args.bar_size,
        whatToShow=args.what,
        useRTH=not args.all_hours,
        formatDate=2,  # intraday bar times come back as UTC datetimes
        timeout=args.timeout,
    )


def main(argv=None, ib=None):
    p = argparse.ArgumentParser(description="Pull historical bars from TWS into CSV.")
    p.add_argument("symbols", nargs="+", help="stock symbols, e.g. AAPL MSFT BRK.B")
    p.add_argument("--duration", default="1 Y", help='how far back: "30 D", "6 M", "1 Y", "5 Y"')
    p.add_argument("--bar-size", default="1 day", help='"1 min", "5 mins", "1 hour", "1 day", "1 week"')
    p.add_argument("--what", default="TRADES", choices=WHAT_TO_SHOW,
                   help="ADJUSTED_LAST gives split/dividend adjusted prices")
    p.add_argument("--all-hours", action="store_true", help="include pre/post market bars")
    p.add_argument("--exchange", default="SMART")
    p.add_argument("--currency", default="USD")
    p.add_argument("--out-dir", default="data", type=Path)
    p.add_argument("--timeout", type=float, default=60, help="seconds to wait per symbol")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7497)
    p.add_argument("--client-id", type=int, default=18)
    args = p.parse_args(argv)

    ib = ib or IB()
    ib.connect(args.host, args.port, clientId=args.client_id, readonly=True, timeout=10)
    failed = []
    try:
        for symbol in args.symbols:
            try:
                bars = pull(ib, symbol, args)
            except LookupError as exc:
                print(f"{symbol}: {exc}")
                failed.append(symbol)
                continue
            if not bars:
                print(f"{symbol}: no bars returned (check market data permissions and the TWS log)")
                failed.append(symbol)
                continue
            path = csv_path(args.out_dir, symbol, args.bar_size, args.duration)
            write_bars(path, bars)
            print(f"{symbol}: {len(bars)} bars {bars[0].date} -> {bars[-1].date}  "
                  f"last close {bars[-1].close}  -> {path}")
    finally:
        ib.disconnect()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
