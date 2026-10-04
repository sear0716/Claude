"""Render a Report as Markdown (terminal-friendly) and CSV."""

from __future__ import annotations

import csv
from pathlib import Path

DISCLAIMER = (
    "_Rule-based technical signals for research only - not investment advice. "
    "Verify prices and levels before placing any order._"
)


def _fmt(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    return str(v)


def _cap(v):
    if not v:
        return "-"
    return f"{v / 1e12:.2f}T" if v >= 1e12 else f"{v / 1e9:.0f}B"


def _table(rows: list[dict], with_levels: bool = True) -> list[str]:
    head = ["#", "Symbol", "Sector", "Action", "Score", "Close", "SMA50", "SMA200", "RSI", "MACD hist", "ATR"]
    if with_levels:
        head += ["Entry", "Stop", "Target", "Shares"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for i, r in enumerate(rows, 1):
        cells = [
            str(i), r["symbol"], r["sector"], f"**{r['action']}**", _fmt(r["score"], 1), _fmt(r["close"]),
            _fmt(r["sma50"]), _fmt(r["sma200"]), _fmt(r["rsi"], 0), _fmt(r["macd_hist"]), _fmt(r["atr"]),
        ]
        if with_levels:
            cells += [_fmt(r["entry"]), _fmt(r["stop"]), _fmt(r["target"]), _fmt(r["shares"])]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def to_markdown(report: dict) -> str:
    out = [
        "# Trading agent report",
        "",
        f"Generated {report['generated_at']} - prices from **{report['price_source']}** - "
        f"sizing on equity **${report['equity']:,.0f}**",
        "",
    ]
    if report["price_source"] == "demo":
        out += ["> **DEMO MODE: prices are synthetic. Nothing below reflects real markets.**", ""]

    macro = report.get("macro")
    if macro:
        out += [f"## Macro regime (FRED): **{macro['regime']}** (score {macro['score']:+d})", ""]
        out += [f"- {n}" for n in macro["notes"]] or ["- no rules triggered"]
        out.append("")
    geo = report.get("geopolitics")
    if geo:
        out += [f"## Geopolitical news (GDELT, 7d): tone {_fmt(geo['avg_tone'])}", ""]
        out += [f"- [{h['title']}]({h['url']})" for h in geo["headlines"]]
        out.append("")

    out += [f"## Top {len(report['top_picks'])} entry candidates", ""]
    out += _table(report["top_picks"]) if report["top_picks"] else ["No stocks currently meet the entry rules."]
    out.append("")
    for r in report["top_picks"]:
        out.append(f"**{r['symbol']}** ({_cap(r.get('market_cap'))}) - " + "; ".join(r["reasons"]))
        news = r.get("news") or {}
        for h in (news.get("headlines") or [])[:2]:
            out.append(f"  - news: [{h['title']}]({h['url']})")
        for fl in ((r.get("fundamentals") or {}).get("recent_filings") or [])[:2]:
            out.append(f"  - SEC {fl['form']} filed {fl['filed']}: {fl['url']}")
        out.append("")

    if report["holdings"]:
        out += ["## Your IBKR positions", ""]
        head = "| Symbol | Qty | Action | Close | Stop (trail) | RSI | Trend | Why |"
        out += [head, "|---|---|---|---|---|---|---|---|"]
        for r in report["holdings"]:
            why = "; ".join(x for x in r["reasons"] if x.startswith("exit")) or "-"
            out.append(
                f"| {r['symbol']} | {_fmt(r['held_quantity'], 0)} | **{r['action']}** | {_fmt(r['close'])} | "
                f"{_fmt(r['stop'])} | {_fmt(r['rsi'], 0)} | {r['trend']} | {why} |"
            )
        out.append("")

    out += ["## Best setup per sector", ""]
    out += _table(list(report["sector_leaders"].values()), with_levels=False)
    out.append("")
    if report["errors"]:
        out += [f"<details><summary>{len(report['errors'])} data warnings</summary>", ""]
        out += [f"- {e}" for e in report["errors"]]
        out += ["", "</details>", ""]
    out.append(DISCLAIMER)
    return "\n".join(out)


def to_csv(report: dict, path: str | Path) -> None:
    cols = [
        "symbol", "sector", "action", "score", "close", "sma50", "sma200", "rsi", "macd", "macd_signal",
        "macd_hist", "atr", "trend", "entry", "stop", "trailing_stop", "target", "shares", "held_quantity",
        "market_cap", "as_of",
    ]
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["universe"])
