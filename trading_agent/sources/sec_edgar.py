"""SEC EDGAR: ticker -> CIK mapping, XBRL company facts and recent filings.

Endpoints (no API key; a descriptive User-Agent is mandatory, max 10 req/s):
  https://www.sec.gov/files/company_tickers.json
  https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
  https://data.sec.gov/submissions/CIK##########.json
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# Tried in order; companies tag revenue differently (banks and REITs especially).
REVENUE_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenuesNetOfInterestExpense",
    "TotalRevenuesAndOtherIncome",
)
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
MATERIAL_FORMS = {"8-K", "10-Q", "10-K", "S-1", "S-3", "SC 13D", "SC 13D/A", "DEF 14A", "4"}


@dataclass
class Fundamentals:
    symbol: str
    cik: int | None = None
    name: str | None = None
    fiscal_year_end: str | None = None
    revenue: float | None = None
    revenue_prev: float | None = None
    revenue_growth: float | None = None
    net_income: float | None = None
    eps_diluted: float | None = None
    shares_outstanding: float | None = None
    recent_filings: list[dict] = field(default_factory=list)

    @property
    def profitable(self) -> bool | None:
        return None if self.net_income is None else self.net_income > 0


def sec_ticker(symbol: str) -> str:
    """SEC uses dashes for share classes: BRK.B -> BRK-B."""
    return symbol.upper().replace(".", "-").replace(" ", "-")


def _annual_values(facts: dict, taxonomy: str, concept: str, unit: str) -> list[dict]:
    """Fiscal-year values from annual reports, one per period end (latest filing wins)."""
    try:
        entries = facts["facts"][taxonomy][concept]["units"][unit]
    except KeyError:
        return []
    by_end: dict[str, dict] = {}
    for e in entries:
        if e.get("form") not in ANNUAL_FORMS or e.get("fp") != "FY":
            continue
        start, end = e.get("start"), e.get("end")
        if start:  # duration facts: keep only ~one-year periods (drop quarters inside 10-Ks)
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            if not 330 <= days <= 400:
                continue
        prev = by_end.get(end)
        if prev is None or e.get("filed", "") >= prev.get("filed", ""):
            by_end[end] = e
    return [by_end[k] for k in sorted(by_end)]


def _latest_instant(facts: dict, taxonomy: str, concept: str, unit: str) -> float | None:
    try:
        entries = facts["facts"][taxonomy][concept]["units"][unit]
    except KeyError:
        return None
    if not entries:
        return None
    latest = max(entries, key=lambda e: (e.get("end", ""), e.get("filed", "")))
    return float(latest["val"])


def parse_company_facts(symbol: str, facts: dict) -> Fundamentals:
    f = Fundamentals(symbol=symbol, cik=facts.get("cik"), name=facts.get("entityName"))
    for concept in REVENUE_CONCEPTS:
        rows = _annual_values(facts, "us-gaap", concept, "USD")
        if rows:
            f.revenue = float(rows[-1]["val"])
            f.fiscal_year_end = rows[-1]["end"]
            if len(rows) >= 2 and rows[-2]["val"]:
                f.revenue_prev = float(rows[-2]["val"])
                f.revenue_growth = f.revenue / f.revenue_prev - 1
            break
    ni = _annual_values(facts, "us-gaap", "NetIncomeLoss", "USD")
    if ni:
        f.net_income = float(ni[-1]["val"])
    eps = _annual_values(facts, "us-gaap", "EarningsPerShareDiluted", "USD/shares")
    if eps:
        f.eps_diluted = float(eps[-1]["val"])
    f.shares_outstanding = _latest_instant(facts, "dei", "EntityCommonStockSharesOutstanding", "shares")
    return f


def parse_recent_filings(submissions: dict, days: int = 30, today: date | None = None) -> list[dict]:
    recent = submissions.get("filings", {}).get("recent", {})
    cutoff = ((today or date.today()) - timedelta(days=days)).isoformat()
    cik = int(submissions.get("cik", 0) or 0)
    out = []
    for form, filed, accession, doc in zip(
        recent.get("form", []),
        recent.get("filingDate", []),
        recent.get("accessionNumber", []),
        recent.get("primaryDocument", []),
    ):
        if filed < cutoff or form not in MATERIAL_FORMS or form == "4":
            continue
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{doc}"
        out.append({"form": form, "filed": filed, "url": url})
    return out


class SecEdgarSource:
    name = "sec_edgar"

    def __init__(self, http):
        self.http = http
        self._tickers: dict[str, dict] | None = None

    def ticker_map(self) -> dict[str, dict]:
        if self._tickers is None:
            raw = self.http.get_json(TICKERS_URL, ttl=24 * 3600)
            self._tickers = {row["ticker"].upper(): row for row in raw.values()}
        return self._tickers

    def cik(self, symbol: str) -> int | None:
        row = self.ticker_map().get(sec_ticker(symbol))
        return int(row["cik_str"]) if row else None

    def fundamentals(self, symbol: str, include_filings: bool = True) -> Fundamentals:
        cik = self.cik(symbol)
        if cik is None:
            return Fundamentals(symbol=symbol)
        facts = self.http.get_json(FACTS_URL.format(cik=cik), ttl=12 * 3600)
        f = parse_company_facts(symbol, facts)
        f.cik = cik
        if include_filings:
            subs = self.http.get_json(SUBMISSIONS_URL.format(cik=cik), ttl=6 * 3600)
            f.recent_filings = parse_recent_filings(subs)
        return f
