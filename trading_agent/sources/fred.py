"""FRED (St. Louis Fed) macro series and a simple risk-on / risk-off regime model.

With FRED_API_KEY set, the JSON API is used; otherwise the public fredgraph CSV
download (no key) is used.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

log = logging.getLogger(__name__)

API_URL = "https://api.stlouisfed.org/fred/series/observations"
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

SERIES = {
    "DFF": "Effective fed funds rate (%)",
    "DGS2": "2-year Treasury yield (%)",
    "DGS10": "10-year Treasury yield (%)",
    "T10Y2Y": "10y-2y Treasury spread (pp)",
    "CPIAUCSL": "CPI, all urban consumers (index)",
    "UNRATE": "Unemployment rate (%)",
    "PAYEMS": "Nonfarm payrolls (thousands)",
    "BAMLH0A0HYM2": "High-yield credit spread (pp)",
    "VIXCLS": "CBOE VIX",
}


@dataclass
class MacroSnapshot:
    latest: dict[str, float] = field(default_factory=dict)
    derived: dict[str, float] = field(default_factory=dict)
    score: int = 0
    regime: str = "neutral"
    notes: list[str] = field(default_factory=list)

    @property
    def size_multiplier(self) -> float:
        return {"risk_on": 1.0, "neutral": 1.0, "risk_off": 0.5}[self.regime]

    @property
    def buy_threshold_adjustment(self) -> float:
        return {"risk_on": -5.0, "neutral": 0.0, "risk_off": 15.0}[self.regime]


def parse_api_observations(payload: dict) -> pd.Series:
    rows = [(o["date"], o["value"]) for o in payload.get("observations", [])]
    s = pd.Series({pd.Timestamp(d): (float(v) if v not in (".", "") else float("nan")) for d, v in rows})
    return s.dropna().sort_index()


def parse_csv(text: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text))
    date_col = df.columns[0]
    value_col = df.columns[1]
    s = pd.to_numeric(df[value_col], errors="coerce")
    s.index = pd.to_datetime(df[date_col])
    return s.dropna().sort_index()


def evaluate_regime(series: dict[str, pd.Series]) -> MacroSnapshot:
    """Score the macro backdrop. Each rule adds/subtracts one point."""
    snap = MacroSnapshot()
    for sid, s in series.items():
        if len(s):
            snap.latest[sid] = float(s.iloc[-1])

    def note(points: int, text: str) -> None:
        snap.score += points
        snap.notes.append(f"{'+' if points > 0 else ''}{points}: {text}")

    curve = snap.latest.get("T10Y2Y")
    if curve is not None:
        if curve < 0:
            note(-1, f"yield curve inverted ({curve:+.2f}pp)")
        elif curve > 0.5:
            note(+1, f"yield curve positively sloped ({curve:+.2f}pp)")

    cpi = series.get("CPIAUCSL")
    if cpi is not None and len(cpi) >= 13:
        yoy = (cpi.iloc[-1] / cpi.iloc[-13] - 1) * 100
        snap.derived["cpi_yoy"] = round(float(yoy), 2)
        if yoy > 3.5:
            note(-1, f"inflation hot ({yoy:.1f}% y/y)")
        elif yoy < 2.5:
            note(+1, f"inflation contained ({yoy:.1f}% y/y)")

    un = series.get("UNRATE")
    if un is not None and len(un) >= 13:
        # Sahm-style: 3-month average vs the low of the prior 12 months.
        rise = float(un.iloc[-3:].mean() - un.iloc[-13:-1].min())
        snap.derived["unemployment_rise_pp"] = round(rise, 2)
        if rise >= 0.5:
            note(-1, f"unemployment rising (+{rise:.2f}pp vs 12m low, Sahm trigger)")

    hy = snap.latest.get("BAMLH0A0HYM2")
    if hy is not None:
        if hy > 5.0:
            note(-1, f"credit spreads wide ({hy:.2f}pp)")
        elif hy < 3.5:
            note(+1, f"credit spreads tight ({hy:.2f}pp)")

    vix = snap.latest.get("VIXCLS")
    if vix is not None:
        if vix > 25:
            note(-1, f"volatility elevated (VIX {vix:.1f})")
        elif vix < 16:
            note(+1, f"volatility calm (VIX {vix:.1f})")

    snap.regime = "risk_on" if snap.score >= 2 else "risk_off" if snap.score <= -2 else "neutral"
    return snap


class FredSource:
    name = "fred"

    def __init__(self, http, api_key: str | None = None):
        self.http = http
        self.api_key = api_key

    def series(self, series_id: str, years: int = 3) -> pd.Series:
        start = (date.today() - timedelta(days=365 * years)).isoformat()
        if self.api_key:
            payload = self.http.get_json(
                API_URL,
                params={
                    "series_id": series_id,
                    "api_key": self.api_key,
                    "file_type": "json",
                    "observation_start": start,
                },
                ttl=6 * 3600,
            )
            return parse_api_observations(payload)
        text = self.http.get_text(CSV_URL, params={"id": series_id, "cosd": start}, ttl=6 * 3600)
        return parse_csv(text)

    def snapshot(self) -> MacroSnapshot:
        data = {}
        for sid in SERIES:
            try:
                data[sid] = self.series(sid)
            except Exception as exc:  # one missing series shouldn't sink the run
                log.warning("FRED %s unavailable: %s", sid, exc)
                data[sid] = pd.Series(dtype=float)
        if not any(len(s) for s in data.values()):
            raise RuntimeError("no FRED series could be downloaded")
        return evaluate_regime(data)
