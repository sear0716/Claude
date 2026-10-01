"""Candidate universe: large-cap US stocks (NYSE + Nasdaq) across all 11 GICS sectors.

Each sector lists more candidates than needed; the agent ranks them by live market
cap (SEC shares outstanding x latest price) and keeps the top `per_sector`.
Edit these lists freely, or pass --symbols / --universe-file on the command line.
"""

from __future__ import annotations

import json
from pathlib import Path

SECTOR_CANDIDATES: dict[str, list[str]] = {
    "Information Technology": [
        "AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "PLTR", "AMD", "CRM", "CSCO", "IBM",
        "ACN", "MU", "ADBE", "QCOM", "TXN", "NOW", "INTU", "AMAT", "LRCX", "KLAC", "ANET",
    ],
    "Communication Services": [
        "GOOGL", "META", "NFLX", "TMUS", "DIS", "T", "VZ", "CMCSA", "CHTR", "EA", "TTWO", "WBD", "LYV",
    ],
    "Consumer Discretionary": [
        "AMZN", "TSLA", "HD", "MCD", "BKNG", "TJX", "LOW", "SBUX", "NKE", "ABNB", "ORLY", "CMG", "MAR", "GM", "F",
    ],
    "Consumer Staples": [
        "WMT", "COST", "PG", "KO", "PEP", "PM", "MO", "MDLZ", "CL", "TGT", "KMB", "KDP", "KR", "GIS",
    ],
    "Energy": ["XOM", "CVX", "COP", "WMB", "EOG", "KMI", "SLB", "MPC", "PSX", "OKE", "VLO", "OXY", "BKR", "FANG"],
    "Financials": [
        "BRK.B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "AXP", "C", "SCHW", "BLK", "SPGI", "PGR", "CB",
    ],
    "Health Care": [
        "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "ISRG", "AMGN", "PFE", "DHR", "BSX", "GILD", "VRTX", "SYK",
    ],
    "Industrials": [
        "GE", "CAT", "RTX", "UBER", "HON", "UNP", "BA", "ETN", "GEV", "LMT", "DE", "ADP", "UPS", "WM", "PH",
    ],
    "Materials": ["LIN", "SHW", "APD", "ECL", "FCX", "NEM", "CTVA", "NUE", "MLM", "VMC", "DOW", "DD", "PPG"],
    "Real Estate": ["PLD", "AMT", "WELL", "EQIX", "SPG", "O", "PSA", "DLR", "CCI", "CBRE", "VICI", "EXR"],
    "Utilities": ["NEE", "SO", "DUK", "CEG", "AEP", "SRE", "VST", "D", "EXC", "XEL", "PCG", "PEG"],
}


def load_universe(path: str | Path | None = None) -> dict[str, list[str]]:
    """Return {sector: [symbols]} from a JSON file, or the built-in lists."""
    if path is None:
        return {k: list(v) for k, v in SECTOR_CANDIDATES.items()}
    data = json.loads(Path(path).read_text())
    return {sector: [s.upper() for s in syms] for sector, syms in data.items()}


def sector_of(symbol: str, universe: dict[str, list[str]]) -> str:
    for sector, syms in universe.items():
        if symbol in syms:
            return sector
    return "Unclassified"
