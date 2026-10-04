"""The orchestrating agent: gather data, compute signals, rank, and report."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import pandas as pd

from .signals import Suggestion, build_suggestion
from .universe import load_universe

log = logging.getLogger(__name__)

BUY_ACTIONS = ("BUY", "BUY_PULLBACK")


@dataclass
class Report:
    generated_at: str
    price_source: str
    equity: float
    account: dict = field(default_factory=dict)
    macro: dict | None = None
    geopolitics: dict | None = None
    top_picks: list[dict] = field(default_factory=list)
    holdings: list[dict] = field(default_factory=list)
    sector_leaders: dict[str, dict] = field(default_factory=dict)
    universe: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class TradingAgent:
    def __init__(
        self,
        cfg,
        prices,
        *,
        broker=None,
        sec=None,
        fred=None,
        gdelt=None,
        universe: dict[str, list[str]] | None = None,
    ):
        self.cfg = cfg
        self.prices = prices
        self.broker = broker
        self.sec = sec
        self.fred = fred
        self.gdelt = gdelt
        self.universe = universe or load_universe()
        self.errors: list[str] = []

    def _try(self, label: str, fn, default=None):
        try:
            return fn()
        except Exception as exc:
            msg = f"{label}: {type(exc).__name__}: {exc}"
            log.warning(msg)
            self.errors.append(msg)
            return default

    # -- pipeline steps ---------------------------------------------------------------
    def _account(self):
        if self.broker is None:
            return {}, [], self.cfg.default_equity
        summary = self._try("IBKR account summary", self.broker.account_summary, {}) or {}
        holdings = self._try("IBKR positions", self.broker.positions, []) or []
        equity = summary.get("NetLiquidation") or self.cfg.default_equity
        return summary, holdings, equity

    def _histories(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        out = {}
        for i, sym in enumerate(symbols, 1):
            log.info("prices %d/%d %s", i, len(symbols), sym)
            df = self._try(f"prices {sym}", lambda s=sym: self.prices.history(s, self.cfg.history_days))
            if df is not None and len(df):
                out[sym] = df
        return out

    def _fundamentals(self, symbols, include_filings: bool) -> dict:
        if self.sec is None:
            return {}
        out = {}
        for sym in symbols:
            f = self._try(f"SEC {sym}", lambda s=sym: self.sec.fundamentals(s, include_filings=include_filings))
            if f is not None:
                out[sym] = f
        return out

    def _rank_by_market_cap(self, histories, fundamentals) -> tuple[dict[str, list[str]], dict[str, float]]:
        caps: dict[str, float] = {}
        for sym, f in fundamentals.items():
            if f.shares_outstanding and sym in histories:
                caps[sym] = f.shares_outstanding * float(histories[sym]["close"].iloc[-1])
        selected = {}
        for sector, syms in self.universe.items():
            available = [s for s in syms if s in histories]
            # Unknown caps keep their seed-list order after the ranked ones.
            ranked = sorted(available, key=lambda s: (s not in caps, -caps.get(s, 0), syms.index(s)))
            selected[sector] = ranked[: self.cfg.per_sector]
        return selected, caps

    def _select_top(self, suggestions: list[Suggestion]) -> list[Suggestion]:
        picks, per_sector = [], {}
        # Ties broken by momentum per unit of volatility (MACD histogram / ATR).
        for s in sorted(suggestions, key=lambda x: (-x.score, -(x.macd_hist / x.atr if x.atr else 0))):
            if s.action not in BUY_ACTIONS:
                continue
            if per_sector.get(s.sector, 0) >= self.cfg.max_per_sector_in_top:
                continue
            picks.append(s)
            per_sector[s.sector] = per_sector.get(s.sector, 0) + 1
            if len(picks) == self.cfg.top_n:
                break
        return picks

    # -- main entry point -------------------------------------------------------------
    def run(self, with_news: bool = True) -> Report:
        self.errors = []
        account, holdings, equity = self._account()
        held = {h.symbol: h for h in holdings}

        macro = self._try("FRED macro", self.fred.snapshot) if self.fred else None
        geo = self._try("GDELT geopolitics", self.gdelt.geopolitical) if (self.gdelt and with_news) else None

        candidates = [s for syms in self.universe.values() for s in syms]
        extra_held = [s for s in held if s not in candidates]
        histories = self._histories(list(dict.fromkeys(candidates + extra_held)))

        fundamentals = self._fundamentals(histories.keys(), include_filings=False)
        selected, caps = self._rank_by_market_cap(histories, fundamentals)
        sector_for = {s: sec for sec, syms in selected.items() for s in syms}
        for s in extra_held:
            sector_for[s] = "Holdings (outside universe)"
        for s in held:  # held names are always analysed, even if they fell out of the top N
            if s in histories and s not in sector_for:
                sector_for[s] = next((sec for sec, syms in self.universe.items() if s in syms), "Holdings")

        def suggest(sym, news=None):
            return self._try(
                f"signals {sym}",
                lambda: build_suggestion(
                    sym,
                    sector_for[sym],
                    histories[sym],
                    self.cfg,
                    equity=equity,
                    macro=macro,
                    fundamentals=fundamentals.get(sym),
                    news=news,
                    holding=held.get(sym),
                ),
            )

        suggestions = {s: sug for s in sector_for if s in histories and (sug := suggest(s)) is not None}
        for sym, sug in suggestions.items():
            sug.market_cap = caps.get(sym)

        # Enrich the shortlist (2x top_n buy candidates + every holding) with news & filings.
        shortlist = [s.symbol for s in self._select_top(list(suggestions.values()))]
        pool = sorted(
            (s for s in suggestions.values() if s.action in BUY_ACTIONS and s.symbol not in shortlist),
            key=lambda s: -s.score,
        )
        shortlist += [s.symbol for s in pool[: self.cfg.top_n]]
        shortlist += [s for s in held if s in suggestions and s not in shortlist]
        if self.sec:
            fundamentals.update(self._fundamentals(shortlist, include_filings=True))
        for sym in shortlist:
            news = None
            if self.gdelt and with_news:
                name = getattr(fundamentals.get(sym), "name", None) or sym
                news = self._try(f"GDELT {sym}", lambda n=name: self.gdelt.company_news(n))
            updated = suggest(sym, news)
            if updated is not None:
                updated.market_cap = caps.get(sym)
                suggestions[sym] = updated

        all_sugs = list(suggestions.values())
        top = self._select_top(all_sugs)
        leaders = {}
        for s in sorted(all_sugs, key=lambda x: -x.score):
            leaders.setdefault(s.sector, s)

        return Report(
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            price_source=getattr(self.prices, "name", type(self.prices).__name__),
            equity=equity,
            account=account,
            macro=asdict(macro) if macro else None,
            geopolitics=asdict(geo) if geo else None,
            top_picks=[s.to_dict() for s in top],
            holdings=[suggestions[s].to_dict() for s in held if s in suggestions],
            sector_leaders={k: v.to_dict() for k, v in leaders.items()},
            universe=[s.to_dict() for s in sorted(all_sugs, key=lambda x: (x.sector, -x.score))],
            errors=self.errors,
        )
