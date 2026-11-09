"""Turn a chain snapshot into a ranked list of cheap contracts.

"Cheap" is measured five independent ways (see README for the research behind each):

  vrp    the expiry's at-the-money implied vol vs a horizon-matched forecast of realised vol
  level  where today's implied vol sits in its own one-year history
  term   the expiry's ATM vol vs the term-structure curve through the other expiries
  smile  the strike's vol vs the smile fitted through the other strikes of that expiry
  hist   the option's average payoff over every same-length window in price history vs its price

A sixth component, the bid-ask spread, subtracts from the score because it is a cost paid up front.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import numpy as np

from . import pricing, volatility as vol
from .models import OptionQuote, Snapshot


@dataclass
class Config:
    rate: float = 0.04  # annual risk-free rate, continuously compounded
    min_dte: int = 7
    max_dte: int = 120
    min_open_interest: int = 100
    min_volume: int = 0
    max_spread_pct: float = 0.25
    min_abs_delta: float = 0.10  # below this, options are lottery tickets and tend to be overpriced
    max_abs_delta: float = 0.60
    history_days: int = 756  # price history used for the historical-payoff test (3 years)
    weights: dict[str, float] = field(default_factory=lambda: {
        "vrp": 0.30, "hist": 0.20, "level": 0.15, "smile": 0.15, "term": 0.10, "spread": 0.10})


@dataclass
class ExpiryStats:
    expiry: dt.date
    dte: int
    t: float
    df: float
    forward: float
    atm_iv: float
    rv_forecast: float | None
    term_fair_iv: float | None = None
    smile: tuple[float, float, float] | None = None  # iv = a + b*x + c*x^2, x in ATM standard deviations
    smile_fair: dict[float, float] = field(default_factory=dict)  # strike -> vol its neighbours imply
    implied_move: float | None = None  # ATM straddle price / forward
    hist_move: float | None = None  # mean |move| over the same horizon in history
    spans_earnings: bool = False

    def curve_iv(self, strike: float) -> float | None:
        if self.smile is None:
            return None
        x = math.log(strike / self.forward) / (self.atm_iv * math.sqrt(self.t))
        a, b, c = self.smile
        return a + b * x + c * x * x if abs(x) <= 2.0 else None

    def smile_iv(self, strike: float) -> float | None:
        return self.smile_fair.get(strike)


@dataclass
class Contract:
    quote: OptionQuote
    dte: int
    iv: float
    atm_iv: float  # of the expiry; compared with the forecast so that skew is not mistaken for cheapness
    iv_ask: float | None
    delta: float
    vega: float
    rv_forecast: float | None
    smile_iv: float | None
    hist_fair: float | None  # average discounted payoff over history, per share
    hist_win: float | None  # share of historical windows that finished above breakeven
    components: dict[str, float]
    score: float
    spans_earnings: bool

    @property
    def verdict(self) -> str:
        return "CHEAP" if self.score >= 65 else "cheap-ish" if self.score >= 55 else "fair" if self.score >= 45 else "rich"

    @property
    def iv_vs_forecast(self) -> float | None:
        return self.atm_iv / self.rv_forecast - 1 if self.rv_forecast else None

    @property
    def hist_edge(self) -> float | None:
        return self.hist_fair / self.quote.mid - 1 if self.hist_fair is not None else None


@dataclass
class Report:
    symbol: str
    asof: dt.date
    spot: float
    hv20: float | None
    hv60: float | None
    hv252: float | None
    yz20: float | None
    iv30: float | None
    forecast30: float | None
    iv_percentile: float | None
    iv_rank: float | None
    level_basis: str
    term_slope: float | None  # longest ATM IV / shortest ATM IV - 1
    skew: float | None  # IV one standard deviation below the forward minus one above
    expiries: list[ExpiryStats]
    contracts: list[Contract]
    notes: list[str]

    @property
    def verdict(self) -> str:
        if self.iv30 is None or self.forecast30 is None:
            return "n/a"
        ratio = self.iv30 / self.forecast30
        low = self.iv_percentile is not None and self.iv_percentile <= 0.30
        if ratio <= 0.95 and low:
            return "CHEAP"
        if ratio <= 1.0 or low:
            return "cheap-ish"
        return "rich" if ratio >= 1.25 else "fair"


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


def _valid(q: OptionQuote) -> bool:
    return q.bid > 0 and q.ask >= q.bid and q.strike > 0


def implied_forward(quotes: list[OptionQuote], spot: float, df: float) -> float:
    """Forward price from put-call parity at the strike where call and put mids are closest."""
    calls = {q.strike: q for q in quotes if q.right == "C" and _valid(q)}
    puts = {q.strike: q for q in quotes if q.right == "P" and _valid(q)}
    fallback = spot / df
    common = [k for k in calls if k in puts]
    if not common:
        return fallback
    k = min(common, key=lambda s: abs(calls[s].mid - puts[s].mid))
    fwd = k + (calls[k].mid - puts[k].mid) / df
    return fwd if abs(fwd / spot - 1) < 0.15 else fallback


def _fit_smile(xs: list[float], ivs: list[float], weights: list[float]) -> tuple[float, float, float] | None:
    """Weighted quadratic through the smile, refitted once without outliers so that a single
    mispriced strike is measured against its neighbours rather than pulled into the curve."""
    if len(xs) < 6:
        return None
    x, y, w = np.array(xs), np.array(ivs), np.sqrt(np.array(weights))
    coeffs = np.polyfit(x, y, 2, w=w)
    resid = y - np.polyval(coeffs, x)
    mad = float(np.median(np.abs(resid - np.median(resid))))
    keep = np.abs(resid) <= max(3 * 1.4826 * mad, 0.005)
    if 6 <= int(keep.sum()) < len(x):
        coeffs = np.polyfit(x[keep], y[keep], 2, w=w[keep])
    c, b, a = (float(v) for v in coeffs)
    return a, b, c


def _neighbour_fair(e: ExpiryStats, rows: list[tuple[OptionQuote, float]]) -> dict[float, float]:
    """Vol each strike should have, judged by the strikes next to it.

    A quadratic never fits a real smile exactly, and where it bends wrongly every strike in that
    region sits off the curve together. That shared gap is fit error, not mispricing, so the curve
    is shifted by the median gap of the two strikes on each side before a strike is compared with it.
    """
    gaps = []
    for q, iv in sorted(rows, key=lambda r: r[0].strike):
        curve = e.curve_iv(q.strike)
        if curve is not None and q.spread_pct <= 0.5:
            gaps.append((q.strike, curve, iv - curve))
    fair = {}
    for i, (strike, curve, _) in enumerate(gaps):
        near = [g[2] for g in gaps[max(0, i - 2):i] + gaps[i + 1:i + 3]]
        if len(near) >= 2:
            fair[strike] = curve + float(np.median(near))
    return fair


def _interp_total_variance(points: list[tuple[float, float]], t: float) -> float | None:
    """IV at maturity t by linear interpolation of total variance; None outside the range."""
    below = [p for p in points if p[0] <= t]
    above = [p for p in points if p[0] >= t]
    if not below or not above:
        return None
    (t0, iv0), (t1, iv1) = max(below), min(above)
    if t1 == t0:
        return iv0
    w = iv0**2 * t0 + (iv1**2 * t1 - iv0**2 * t0) * (t - t0) / (t1 - t0)
    return math.sqrt(w / t) if w > 0 else None


def analyze(snap: Snapshot, cfg: Config | None = None, iv_history: list[float] | None = None) -> Report:
    cfg = cfg or Config()
    notes = list(snap.notes)
    closes = [b.close for b in snap.bars]
    by_expiry: dict[dt.date, list[OptionQuote]] = {}
    for q in snap.quotes:
        if cfg.min_dte <= (q.expiry - snap.asof).days <= cfg.max_dte:
            by_expiry.setdefault(q.expiry, []).append(q)
    if snap.quotes and sum(1 for q in snap.quotes if _valid(q)) < 0.3 * len(snap.quotes):
        notes.append("most quotes have no bid/ask - the market is probably closed; rerun during trading hours")

    expiries: list[ExpiryStats] = []
    otm_ivs: dict[dt.date, list[tuple[OptionQuote, float]]] = {}
    for expiry in sorted(by_expiry):
        quotes = by_expiry[expiry]
        dte = (expiry - snap.asof).days
        t = dte / 365.0
        df = math.exp(-cfg.rate * t)
        fwd = implied_forward(quotes, snap.spot, df)
        rows = []
        for q in quotes:
            is_otm = q.strike >= fwd if q.right == "C" else q.strike < fwd
            if not (is_otm and _valid(q)):
                continue
            iv = pricing.implied_vol(q.mid, fwd, q.strike, t, df, q.right)
            if iv is not None:
                rows.append((q, iv))
        if len(rows) < 2:
            continue
        nearest_c = min((r for r in rows if r[0].right == "C"), key=lambda r: r[0].strike, default=None)
        nearest_p = max((r for r in rows if r[0].right == "P"), key=lambda r: r[0].strike, default=None)
        near = [r[1] for r in (nearest_c, nearest_p) if r]
        atm0 = sum(near) / len(near)
        scale = atm0 * math.sqrt(t)
        fit_rows = [(math.log(q.strike / fwd) / scale, iv, 1.0 / (min(q.spread_pct, 1.0) + 0.05))
                    for q, iv in rows if q.spread_pct <= 0.5]
        fit_rows = [r for r in fit_rows if abs(r[0]) <= 2.0]
        smile = _fit_smile(*map(list, zip(*fit_rows))) if fit_rows else None
        atm = atm0
        if smile and abs(smile[0] / atm0 - 1) < 0.2:
            # Re-express the fit in units of the final ATM vol so smile_iv() can use atm_iv directly.
            atm = smile[0]
            s = atm / atm0
            smile = (smile[0], smile[1] * s, smile[2] * s * s)
        else:
            smile = None
        h = max(1, round(dte * vol.TRADING_DAYS / 365))
        stats = ExpiryStats(expiry, dte, t, df, fwd, atm, vol.forecast(closes, h), smile=smile,
                            spans_earnings=bool(snap.earnings_date and snap.asof <= snap.earnings_date <= expiry))
        stats.implied_move = 2 * (2 * pricing.norm_cdf(0.5 * atm * math.sqrt(t)) - 1)
        rets = _history_returns(closes, h, cfg.history_days)
        if rets is not None:
            stats.hist_move = float(np.mean(np.abs(rets - 1)))
        stats.smile_fair = _neighbour_fair(stats, rows)
        expiries.append(stats)
        otm_ivs[expiry] = rows

    curve = [(e.t, e.atm_iv) for e in expiries]
    for e in expiries:
        e.term_fair_iv = _interp_total_variance([p for p in curve if p[0] != e.t], e.t)

    iv30 = _interp_total_variance(curve, 30 / 365.0) if curve else None
    if iv30 is None and curve:
        iv30 = min(curve, key=lambda p: abs(p[0] - 30 / 365.0))[1]
    percentile = rank = None
    basis = "n/a"
    if iv30 is not None:
        if iv_history and len(iv_history) >= 30:
            hist, basis = iv_history[-252:], f"own IV history ({min(len(iv_history), 252)} days)"
        else:
            hist, basis = vol.rolling_close_to_close(closes, 21)[-252:], "realised-vol proxy"
        if hist:
            percentile = vol.percentile_of(iv30, hist)
            lo, hi = min(hist), max(hist)
            rank = _clip((iv30 - lo) / (hi - lo)) if hi > lo else None
            rank = max(rank, 0.0) if rank is not None else None

    contracts: list[Contract] = []
    for e in expiries:
        rets = _history_returns(closes, max(1, round(e.dte * vol.TRADING_DAYS / 365)), cfg.history_days)
        for q, iv in otm_ivs[e.expiry]:
            delta = pricing.delta(e.forward, q.strike, iv, e.t, q.right)
            if not (cfg.min_abs_delta <= abs(delta) <= cfg.max_abs_delta):
                continue
            if q.open_interest < cfg.min_open_interest or q.volume < cfg.min_volume or q.spread_pct > cfg.max_spread_pct:
                continue
            contracts.append(_score(q, iv, delta, e, rets, percentile, cfg))
    contracts.sort(key=lambda c: c.score, reverse=True)

    skew = None
    front = next((e for e in expiries if e.smile), None)
    if front:
        a, b, c = front.smile
        skew = (a - b + c) - (a + b + c)
    return Report(
        symbol=snap.symbol, asof=snap.asof, spot=snap.spot,
        hv20=vol.close_to_close(closes, 20), hv60=vol.close_to_close(closes, 60),
        hv252=vol.close_to_close(closes, 252), yz20=vol.yang_zhang(snap.bars, 20),
        iv30=iv30, forecast30=vol.forecast(closes, 21), iv_percentile=percentile, iv_rank=rank,
        level_basis=basis,
        term_slope=expiries[-1].atm_iv / expiries[0].atm_iv - 1 if len(expiries) >= 2 else None,
        skew=skew, expiries=expiries, contracts=contracts, notes=notes,
    )


def _history_returns(closes: list[float], horizon: int, lookback: int):
    """Gross returns over every `horizon`-day window, rescaled to average 1.

    Rescaling removes the stock's historical drift, so the test asks "was this much movement
    common?" and never "did the stock go up?" - it prices the option on the forward, like the market.
    """
    rets = vol.horizon_gross_returns(closes[-(lookback + 1):], horizon)
    if len(rets) < max(60, 4 * horizon):
        return None
    arr = np.array(rets)
    return arr / arr.mean()


def _score(q: OptionQuote, iv: float, delta: float, e: ExpiryStats, rets, percentile: float | None,
           cfg: Config) -> Contract:
    comp: dict[str, float] = {}
    if e.rv_forecast:
        # At-the-money vol, not the strike's own: out-of-the-money calls always carry a lower vol
        # than the forecast because of skew, which the hist and smile measures judge strike by strike.
        comp["vrp"] = _clip(math.log(e.rv_forecast / e.atm_iv) / 0.30)
    if percentile is not None:
        comp["level"] = 1.0 - 2.0 * percentile
    if e.term_fair_iv:
        comp["term"] = _clip((1.0 - e.atm_iv / e.term_fair_iv) / 0.10)
    smile_iv = e.smile_iv(q.strike)
    if smile_iv:
        # Judge the residual against the option's own bid-ask width in vol terms: a gap smaller
        # than the spread is noise, a gap of two half-spreads or more is a real dislocation.
        iv_bid = pricing.implied_vol(q.bid, e.forward, q.strike, e.t, e.df, q.right) or iv
        iv_ask_ = pricing.implied_vol(q.ask, e.forward, q.strike, e.t, e.df, q.right) or iv
        half_width = max(0.5 * (iv_ask_ - iv_bid), 0.0025)
        comp["smile"] = _clip((smile_iv - iv) / (2.0 * half_width))
    hist_fair = hist_win = None
    if rets is not None:
        terminal = e.forward * rets
        payoff = np.maximum(terminal - q.strike, 0) if q.right == "C" else np.maximum(q.strike - terminal, 0)
        hist_fair = float(e.df * payoff.mean())
        hist_win = float((payoff > q.mid / e.df).mean())
        comp["hist"] = _clip((hist_fair / q.mid - 1.0) / 0.50)
    comp["spread"] = -_clip(q.spread_pct / 0.20)
    total_w = sum(cfg.weights[k] for k in comp)
    score = 50.0 + 50.0 * sum(cfg.weights[k] * v for k, v in comp.items()) / total_w
    return Contract(
        quote=q, dte=e.dte, iv=iv, atm_iv=e.atm_iv,
        iv_ask=pricing.implied_vol(q.ask, e.forward, q.strike, e.t, e.df, q.right),
        delta=delta, vega=pricing.vega(e.forward, q.strike, iv, e.t, e.df) / 100.0,
        rv_forecast=e.rv_forecast, smile_iv=smile_iv, hist_fair=hist_fair, hist_win=hist_win,
        components=comp, score=score, spans_earnings=e.spans_earnings,
    )
