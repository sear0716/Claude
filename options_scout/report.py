"""Markdown / CSV / JSON output."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

DISCLAIMER = (
    "_Research only, not investment advice. Cheap implied volatility is not a forecast that the "
    "option will make money: the stock still has to move more than the market expects. Check quotes, "
    "earnings dates and liquidity before trading. This tool never places orders._"
)

CONTRACT_COLUMNS = [
    "symbol", "expiry", "days", "strike", "right", "bid", "ask", "mid", "spread_pct", "iv_bid", "iv_mid",
    "iv_ask", "rv_forecast", "iv_rv", "vol_gap", "smile_resid", "delta", "vega", "model_price", "edge_pct",
    "open_interest", "volume", "source_iv", "score", "cheap", "passes",
]


def _pct(v, nd=1):
    return "-" if v is None or (isinstance(v, float) and not math.isfinite(v)) else f"{v * 100:.{nd}f}%"


def _num(v, nd=2):
    return "-" if v is None or (isinstance(v, float) and not math.isfinite(v)) else f"{v:,.{nd}f}"


def summary_table(summaries) -> list[str]:
    head = ["Symbol", "Spot", "RV20", "YZ20", "RV 1y", "RV fcst 30d", "ATM IV 30d", "IV/RV", "IV rank",
            "IV pctl", "Quoted", "Pass filters", "Cheap"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for s in summaries:
        lines.append("| " + " | ".join([
            s.symbol, _num(s.spot), _pct(s.rv20), _pct(s.yz20), _pct(s.rv252), _pct(s.rv_forecast30),
            _pct(s.atm_iv30), _num(s.iv_rv_ratio30), _num(s.iv_rank, 0), _num(s.iv_percentile, 0),
            str(s.contracts_quoted), str(s.contracts_passing), str(s.contracts_cheap),
        ]) + " |")
    return lines


def contract_table(top: pd.DataFrame) -> list[str]:
    head = ["#", "Contract", "DTE", "Bid/Ask", "IV ask", "RV fcst", "Vol gap", "Smile", "Delta", "Model px",
            "Edge", "OI", "Score"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for i, r in top.iterrows():
        name = f"{r['symbol']} {pd.Timestamp(r['expiry']):%Y-%m-%d} {r['strike']:g}{r['right']}"
        lines.append("| " + " | ".join([
            str(i + 1), name, f"{r['days']:.0f}", f"{r['bid']:.2f}/{r['ask']:.2f}", _pct(r["iv_ask"]),
            _pct(r["rv_forecast"]), _pct(r["vol_gap"]),
            "-" if not math.isfinite(r["smile_resid"]) else f"{r['smile_resid'] * 100:+.1f} pts",
            f"{r['delta']:+.2f}", _num(r["model_price"]), _pct(r["edge_pct"], 0),
            _num(r["open_interest"], 0), f"{r['score']:.0f}",
        ]) + " |")
    return lines


def to_markdown(summaries, top: pd.DataFrame, meta: dict) -> str:
    out = [
        "# Cheap options scan",
        "",
        f"Source: **{meta['source']}** · risk-free rate {meta['rate'] * 100:.2f}% · "
        f"{meta['min_dte']}-{meta['max_dte']} days to expiry · generated {meta['generated']}",
        "",
        "## Stocks",
        "",
        *summary_table(summaries),
        "",
    ]
    notes = [f"- **{s.symbol}**: {n}" for s in summaries for n in s.notes]
    if notes:
        out += ["Notes:", "", *notes, ""]
    out += ["## Cheapest contracts (IV at the ask is below forecast realized vol)", ""]
    if top.empty:
        out += ["No contract passed the liquidity filters with IV(ask) below forecast realized vol.", ""]
    else:
        out += [*contract_table(top), ""]
    out += [
        "**Reading it:** *Vol gap* = 1 - IV(ask)/forecast RV (positive = you buy volatility below what the "
        "stock is expected to realize). *Smile* = IV(mid) minus the fitted smile at that strike, in vol points "
        "(negative = cheap against neighbouring strikes). *Model px* = Black-Scholes value using forecast RV; "
        "*Edge* = model px / ask - 1. *IV rank* = where today's 30-day ATM IV sits in its 1-year range "
        "(0 = lowest).",
        "",
        DISCLAIMER,
        "",
    ]
    return "\n".join(out)


def write_reports(out_dir: Path, summaries, contracts: pd.DataFrame, top: pd.DataFrame, meta: dict) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "markdown": out_dir / "cheap_options.md",
        "contracts_csv": out_dir / "options_scan.csv",
        "json": out_dir / "cheap_options.json",
    }
    paths["markdown"].write_text(to_markdown(summaries, top, meta))
    if not contracts.empty:
        cols = [c for c in CONTRACT_COLUMNS if c in contracts]
        contracts.sort_values("score", ascending=False)[cols].to_csv(paths["contracts_csv"], index=False)
    payload = {
        "meta": meta,
        "stocks": [s.__dict__ for s in summaries],
        "top": json.loads(top[[c for c in CONTRACT_COLUMNS if c in top]].to_json(orient="records", date_format="iso"))
        if not top.empty else [],
    }
    paths["json"].write_text(json.dumps(payload, indent=2, default=str))
    return paths
