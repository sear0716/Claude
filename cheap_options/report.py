"""Terminal, CSV and JSON output."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .analysis import Contract, Report


def _pct(x: float | None, digits: int = 1, signed: bool = False) -> str:
    if x is None:
        return "-"
    return f"{x * 100:{'+' if signed else ''}.{digits}f}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    lines = ["  ".join(h.rjust(w) for h, w in zip(headers, widths)), "  ".join("-" * w for w in widths)]
    lines += ["  ".join(c.rjust(w) for c, w in zip(r, widths)) for r in rows]
    return "\n".join(lines)


def contract_row(symbol: str, c: Contract) -> dict:
    q = c.quote
    return {
        "symbol": symbol, "expiry": q.expiry.isoformat(), "dte": c.dte, "strike": q.strike, "right": q.right,
        "bid": q.bid, "ask": q.ask, "mid": round(q.mid, 4), "iv": round(c.iv, 4), "atm_iv": round(c.atm_iv, 4),
        "iv_ask": round(c.iv_ask, 4) if c.iv_ask else None,
        "rv_forecast": round(c.rv_forecast, 4) if c.rv_forecast else None,
        "atm_iv_vs_forecast": round(c.iv_vs_forecast, 4) if c.iv_vs_forecast is not None else None,
        "iv_vs_smile": round(c.iv - c.smile_iv, 4) if c.smile_iv else None,
        "hist_fair": round(c.hist_fair, 4) if c.hist_fair is not None else None,
        "hist_edge": round(c.hist_edge, 4) if c.hist_edge is not None else None,
        "hist_win": round(c.hist_win, 4) if c.hist_win is not None else None,
        "delta": round(c.delta, 3), "vega": round(c.vega, 4), "spread_pct": round(q.spread_pct, 4),
        "volume": q.volume, "open_interest": q.open_interest, "score": round(c.score, 1),
        "verdict": c.verdict, "spans_earnings": c.spans_earnings,
        **{f"c_{k}": round(v, 3) for k, v in c.components.items()},
    }


def render(reports: list[Report], ranked: list[tuple[str, Contract]]) -> str:
    out = ["UNDERLYINGS  (vols in %, annualised)"]
    out.append(_table(
        ["Sym", "Spot", "IV30", "Fcst", "IV/Fcst", "HV20", "HV60", "HV1y", "YZ20", "IV%ile", "Term", "Skew", "Options"],
        [[r.symbol, f"{r.spot:.2f}", _pct(r.iv30), _pct(r.forecast30),
          f"{r.iv30 / r.forecast30:.2f}" if r.iv30 and r.forecast30 else "-",
          _pct(r.hv20), _pct(r.hv60), _pct(r.hv252), _pct(r.yz20), _pct(r.iv_percentile, 0),
          _pct(r.term_slope, 0, True), _pct(r.skew, 1, True), r.verdict] for r in reports]))
    bases = sorted({f"{r.symbol}: {r.level_basis}" for r in reports})
    out.append("IV%ile basis - " + "; ".join(bases))

    out.append("\nEXPIRIES  (implied move = ATM straddle / forward; hist move = average absolute move over the same number of days)")
    out.append(_table(
        ["Sym", "Expiry", "DTE", "ATM IV", "Fcst RV", "Term fair", "Impl move", "Hist move", "Hist/Impl", "Earn"],
        [[r.symbol, e.expiry.isoformat(), str(e.dte), _pct(e.atm_iv), _pct(e.rv_forecast), _pct(e.term_fair_iv),
          _pct(e.implied_move), _pct(e.hist_move),
          f"{e.hist_move / e.implied_move:.2f}" if e.hist_move and e.implied_move else "-",
          "yes" if e.spans_earnings else ""] for r in reports for e in r.expiries]))

    out.append(f"\nCHEAPEST CONTRACTS  (top {len(ranked)}; score 0-100, 50 = fairly priced)")
    out.append(_table(
        ["Sym", "Expiry", "DTE", "Strike", "", "Bid", "Ask", "IV", "ATM IV", "Fcst", "ATMvFc%", "vsSmile", "HistEV%", "Win%",
         "Delta", "Sprd%", "OI", "Score", "Verdict"],
        [[s, c.quote.expiry.isoformat(), str(c.dte), f"{c.quote.strike:g}", c.quote.right,
          f"{c.quote.bid:.2f}", f"{c.quote.ask:.2f}", _pct(c.iv), _pct(c.atm_iv), _pct(c.rv_forecast), _pct(c.iv_vs_forecast, 0, True),
          _pct(c.iv - c.smile_iv, 1, True) if c.smile_iv else "-", _pct(c.hist_edge, 0, True), _pct(c.hist_win, 0),
          f"{c.delta:+.2f}", _pct(c.quote.spread_pct, 0), str(c.quote.open_interest), f"{c.score:.0f}",
          c.verdict + (" (E)" if c.spans_earnings else "")] for s, c in ranked]))
    out.append("ATMvFc% = the expiry's at-the-money implied vol vs forecast realised vol; vsSmile = vol points vs what neighbouring strikes imply; "
               "HistEV% = historical average payoff vs the ask price; Win% = share of past windows whose payoff beat the ask; "
               "(E) = expiry spans an earnings date, where implied vol is normally elevated.")
    notes = [f"{r.symbol}: {n}" for r in reports for n in r.notes]
    if notes:
        out.append("\nNOTES\n" + "\n".join(notes))
    out.append("\nA statistical screen, not investment advice. Options are on average priced above their realised "
               "payoff; a high score means less expensive than usual, not a sure profit.")
    return "\n".join(out)


def write_csv(path: Path, ranked: list[tuple[str, Contract]]) -> None:
    rows = [contract_row(s, c) for s, c in ranked]
    fields: list[str] = []
    for row in rows:
        fields += [k for k in row if k not in fields]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, reports: list[Report], ranked: list[tuple[str, Contract]]) -> None:
    payload = {
        "underlyings": [{
            "symbol": r.symbol, "asof": r.asof.isoformat(), "spot": r.spot, "iv30": r.iv30, "forecast30": r.forecast30,
            "hv20": r.hv20, "hv60": r.hv60, "hv252": r.hv252, "yz20": r.yz20, "iv_percentile": r.iv_percentile,
            "iv_rank": r.iv_rank, "level_basis": r.level_basis, "term_slope": r.term_slope, "skew": r.skew,
            "verdict": r.verdict, "notes": r.notes,
            "earnings_date": r.earnings_date.isoformat() if r.earnings_date else None,
            "earnings_move": r.earnings_move, "ex_earnings_vol": r.ex_earnings_vol,
            "expiries": [{
                "expiry": e.expiry.isoformat(), "dte": e.dte, "forward": e.forward, "atm_iv": e.atm_iv,
                "rv_forecast": e.rv_forecast, "term_fair_iv": e.term_fair_iv, "implied_move": e.implied_move,
                "hist_move": e.hist_move, "spans_earnings": e.spans_earnings} for e in r.expiries],
        } for r in reports],
        "contracts": [contract_row(s, c) for s, c in ranked],
    }
    path.write_text(json.dumps(payload, indent=2))
