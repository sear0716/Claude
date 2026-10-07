"""Performance stats for round-trip trades (live trades.csv or backtest results) + HTML dashboard."""
import datetime as dt
import html
import json
import math
from collections import defaultdict

R_BUCKETS = [(-math.inf, -1), (-1, -0.5), (-0.5, 0), (0, 0.5), (0.5, 1), (1, 2), (2, math.inf)]


def round_trips_from_fills(rows):
    """Pair BUY fills with the SELL fills that close them, per symbol, in time order."""
    open_ = {}
    trips = []
    for r in sorted(rows, key=lambda r: dt.datetime.fromisoformat(r["timestamp_iso"])):
        if r.get("status") != "Filled" or not r.get("size"):
            continue
        sym, side, qty, px = r["symbol"], r["side"], int(float(r["size"])), float(r["fill_price"])
        if side == "BUY":
            open_[sym] = dict(symbol=sym, entry_time=r["timestamp_iso"], qty=qty, entry_price=px,
                              initial_stop=float(r.get("initial_stop") or 0), remaining=qty, proceeds=0.0,
                              exits=[])
        elif sym in open_:
            t = open_[sym]
            t["remaining"] -= qty
            t["proceeds"] += qty * px
            t["exits"].append(r.get("reason", ""))
            if t["remaining"] <= 0:
                trips.append(_close_trip(t, r["timestamp_iso"]))
                del open_[sym]
    return trips, list(open_.values())


def _close_trip(t, exit_time):
    exit_price = t["proceeds"] / t["qty"]
    risk = t["entry_price"] - t["initial_stop"]
    return dict(symbol=t["symbol"], entry_time=t["entry_time"], exit_time=exit_time, qty=t["qty"],
                entry_price=round(t["entry_price"], 4), exit_price=round(exit_price, 4),
                pnl=round(t["proceeds"] - t["qty"] * t["entry_price"], 2),
                r=round((exit_price - t["entry_price"]) / risk, 3) if risk > 0 else 0.0,
                exit_reason="+".join(x for x in t["exits"] if x))


def summarize(trips, starting_equity=None):
    n = len(trips)
    if not n:
        return dict(trades=0)
    pnls = [t["pnl"] for t in trips]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_loss = -sum(losses)
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    hist = defaultdict(int)
    for t in trips:
        for lo, hi in R_BUCKETS:
            if lo <= t["r"] < hi:
                hist[_bucket_label(lo, hi)] += 1
    out = dict(
        trades=n,
        win_rate_pct=round(100 * len(wins) / n, 1),
        total_pnl=round(sum(pnls), 2),
        avg_pnl=round(sum(pnls) / n, 2),
        avg_r=round(sum(t["r"] for t in trips) / n, 3),
        profit_factor=round(sum(wins) / gross_loss, 2) if gross_loss > 0 else None,
        largest_winner=round(max(pnls), 2),
        largest_loser=round(min(pnls), 2),
        max_drawdown=round(max_dd, 2),
        r_histogram={_bucket_label(lo, hi): hist.get(_bucket_label(lo, hi), 0) for lo, hi in R_BUCKETS},
    )
    if starting_equity:
        out["return_pct"] = round(100 * sum(pnls) / starting_equity, 2)
        out["max_drawdown_pct"] = round(100 * max_dd / starting_equity, 2)
    return out


def _bucket_label(lo, hi):
    if lo == -math.inf:
        return f"< {hi}R"
    if hi == math.inf:
        return f">= {lo}R"
    return f"{lo}R to {hi}R"


def render_dashboard(stats, trips, open_positions, title="Gap bot (paper)"):
    def table(rows, cols):
        head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
        body = "".join("<tr>" + "".join(f"<td>{html.escape(str(r.get(c, '')))}</td>" for c in cols) + "</tr>"
                       for r in rows)
        return f'<table class="table table-sm table-striped"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'

    stat_rows = [dict(metric=k, value=v) for k, v in stats.items() if k != "r_histogram"]
    hist_rows = [dict(bucket=k, trades=v) for k, v in stats.get("r_histogram", {}).items()]
    generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"></head>
<body class="p-4"><div class="container">
<h1>{html.escape(title)}</h1><p class="text-muted">Generated {generated}</p>
<h3>Summary</h3>{table(stat_rows, ["metric", "value"])}
<h3>R-multiple histogram</h3>{table(hist_rows, ["bucket", "trades"])}
<h3>Open positions</h3>{table(open_positions, ["symbol", "qty", "entry_price", "stop", "partial_done", "breakeven_done"])}
<h3>Recent closed trades</h3>{table(list(reversed(trips))[:50], ["symbol", "entry_time", "exit_time", "qty", "entry_price", "exit_price", "pnl", "r", "exit_reason"])}
</div></body></html>"""


def daily_report(state, settings, out_dir):
    trips, _ = round_trips_from_fills(state.read_trades())
    stats = summarize(trips, settings.portfolio_value)
    positions = [p.to_dict() for p in state.load_positions().values()]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(render_dashboard(stats, trips, positions))
    (out_dir / "stats.json").write_text(json.dumps(stats, indent=2))
    return stats
