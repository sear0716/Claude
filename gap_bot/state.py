"""On-disk state: open positions (JSON, written atomically), trades.csv, decision log."""
import csv
import datetime as dt
import json
import os
from pathlib import Path

from .strategy import Position

TRADE_FIELDS = ["timestamp_iso", "symbol", "side", "size", "fill_price", "order_id", "status", "reason", "initial_stop"]


class State:
    def __init__(self, state_dir, log_dir):
        self.state_dir = Path(state_dir)
        self.log_dir = Path(log_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.positions_file = self.state_dir / "open_positions.json"
        self.trades_file = self.state_dir / "trades.csv"
        self.watchlist_file = self.state_dir / "watchlist.txt"
        self.decision_log = self.log_dir / "safety_log.jsonl"

    # positions -------------------------------------------------------------
    def load_positions(self):
        if not self.positions_file.exists():
            return {}
        data = json.loads(self.positions_file.read_text() or "{}")
        return {sym: Position(**p) for sym, p in data.items()}

    def save_positions(self, positions):
        tmp = self.positions_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({s: p.to_dict() for s, p in positions.items()}, indent=2))
        os.replace(tmp, self.positions_file)

    # trades ------------------------------------------------------------------
    def record_trade(self, **row):
        new = not self.trades_file.exists()
        row.setdefault("timestamp_iso", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        with open(self.trades_file, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=TRADE_FIELDS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(row)

    def read_trades(self):
        if not self.trades_file.exists():
            return []
        with open(self.trades_file, newline="") as f:
            return list(csv.DictReader(f))

    def _buys_on(self, today):
        from .strategy import ET
        return [r for r in self.read_trades()
                if r["side"] == "BUY" and r["status"] == "Filled"
                and dt.datetime.fromisoformat(r["timestamp_iso"]).astimezone(ET).date() == today]

    def entries_today(self, today):
        """Number of BUY fills recorded on `today` (an ET date) - enforces max trades per day."""
        return len(self._buys_on(today))

    def symbols_entered_today(self, today):
        """Symbols already bought today; the bot takes at most one entry per symbol per day."""
        return {r["symbol"] for r in self._buys_on(today)}

    # watchlist / log ---------------------------------------------------------
    def write_watchlist(self, rows):
        lines = [f"{r['symbol']}\t{r['gap_pct']:.2f}\t{r['price']:.2f}" for r in rows]
        self.watchlist_file.write_text("\n".join(lines) + ("\n" if lines else ""))

    def read_watchlist(self):
        if not self.watchlist_file.exists():
            return []
        return [ln.split("\t")[0] for ln in self.watchlist_file.read_text().splitlines() if ln.strip()]

    def log_decision(self, **entry):
        entry.setdefault("ts", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        with open(self.decision_log, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
