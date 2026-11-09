"""Risk-free rate for pricing: 3-month T-bill from FRED's public CSV, else a default."""

from __future__ import annotations

import io
import logging
import math

import pandas as pd

log = logging.getLogger(__name__)

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
DEFAULT_RATE = 0.04


def risk_free_rate(session=None, series: str = "DTB3") -> float:
    """Latest 3-month T-bill yield as a continuously compounded decimal."""
    try:
        import requests

        http = session or requests
        resp = http.get(FRED_CSV, params={"id": series}, timeout=15)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        values = pd.to_numeric(df.iloc[:, 1], errors="coerce").dropna()
        pct = float(values.iloc[-1])
        return math.log1p(pct / 100.0)
    except Exception as exc:  # noqa: BLE001 - any network/parse failure falls back
        log.warning("Could not fetch the T-bill rate from FRED (%s); using %.2f%%", exc, DEFAULT_RATE * 100)
        return DEFAULT_RATE
