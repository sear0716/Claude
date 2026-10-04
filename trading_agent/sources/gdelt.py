"""GDELT DOC 2.0 API: news volume, tone and headlines for companies and global events.

https://api.gdeltproject.org/api/v2/doc/doc  (no key; GDELT asks for <= 1 request / 5 s)
Tone is roughly -10 (very negative) .. +10 (very positive); most news sits in -3..+3.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

GEOPOLITICAL_QUERY = (
    '(sanctions OR tariffs OR "trade war" OR invasion OR "military strike" OR '
    '"central bank" OR "debt ceiling" OR "government shutdown") sourcelang:english'
)

_SUFFIX = re.compile(
    r"[,.]?\s+(inc|incorporated|corp|corporation|co|company|ltd|plc|holdings?|group|n\.?v|s\.?a|lp|llc|the)\.?$",
    re.IGNORECASE,
)


@dataclass
class NewsSignal:
    query: str
    avg_tone: float | None = None
    article_count: int = 0
    headlines: list[dict] = field(default_factory=list)


def company_query(name: str) -> str:
    """'Apple Inc.' -> '"Apple" sourcelang:english' (GDELT matches exact phrases)."""
    clean = name.strip()
    for _ in range(3):
        clean = _SUFFIX.sub("", clean).strip(" ,.")
    clean = clean.replace('"', "")
    return f'"{clean}" sourcelang:english'


def parse_tone(payload: dict) -> float | None:
    values = []
    for series in payload.get("timeline", []):
        values.extend(float(p["value"]) for p in series.get("data", []) if p.get("value") is not None)
    return round(sum(values) / len(values), 3) if values else None


def parse_articles(payload: dict, limit: int = 5) -> list[dict]:
    return [
        {"title": a.get("title"), "url": a.get("url"), "domain": a.get("domain"), "seen": a.get("seendate")}
        for a in payload.get("articles", [])[:limit]
    ]


class GdeltSource:
    name = "gdelt"

    def __init__(self, http):
        self.http = http

    def _doc(self, query: str, mode: str, timespan: str, **extra) -> dict:
        params = {"query": query, "mode": mode, "format": "json", "timespan": timespan, **extra}
        text = self.http.get_text(DOC_URL, params=params, ttl=3 * 3600)
        if not text.strip().startswith("{"):
            # GDELT reports query errors as plain text with HTTP 200.
            raise ValueError(f"GDELT: {text.strip()[:200]}")
        return json.loads(text)

    def news(self, query: str, timespan: str = "7d", headlines: int = 5) -> NewsSignal:
        sig = NewsSignal(query=query)
        sig.avg_tone = parse_tone(self._doc(query, "timelinetone", timespan))
        arts = self._doc(query, "artlist", timespan, maxrecords=max(headlines, 25), sort="datedesc")
        sig.article_count = len(arts.get("articles", []))
        sig.headlines = parse_articles(arts, headlines)
        return sig

    def company_news(self, company_name: str, timespan: str = "7d") -> NewsSignal:
        return self.news(company_query(company_name), timespan)

    def geopolitical(self, timespan: str = "7d") -> NewsSignal:
        return self.news(GEOPOLITICAL_QUERY, timespan)
