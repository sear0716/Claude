"""Score every listed option on how cheap its implied volatility is.

For each stock:
  1. Realized-vol profile from daily OHLC (close-to-close, Yang-Zhang, EWMA) and a
     mean-reverting forecast of the average vol over each option's remaining life.
  2. Implied vol of every contract at the bid, mid and ask (own BSM inversion, so
     every data source is treated the same way).
  3. Per contract:
       vol gap     = 1 - IV(ask) / forecast RV   > 0 means you can BUY below expected vol
       model edge  = (BSM price at forecast RV - ask) / ask
       smile resid = IV(mid) - fitted smile at that strike (negative = cheap vs its neighbours)
  4. Per stock: 30-day ATM IV vs realized, and IV rank / percentile over the last year.
A composite score ranks contracts; `cheap` is the strict test IV(ask) < forecast RV.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .ivstore import IVStore
from .pricing import bsm_delta, bsm_price, bsm_vega, implied_vol
from .volatility import VolProfile, iv_rank, vol_profile

log = logging.getLogger(__name__)


@dataclass
class ScanParams:
    min_dte: int = 7
    max_dte: int = 120
    lo_moneyness: float = 0.75  # strikes from 75% ...
    hi_moneyness: float = 1.25  # ... to 125% of spot
    min_open_interest: float = 100
    min_volume: float = 0
    max_spread_pct: float = 0.15  # (ask - bid) / mid
    min_bid: float = 0.05
    min_abs_delta: float = 0.10
    max_abs_delta: float = 0.80
    rate: float = 0.04
    div_yield: float | None = None  # None: ask the source, else 0
    half_life_days: float = 30.0
    history_days: int = 400


@dataclass
class SymbolSummary:
    symbol: str
    spot: float
    div_yield: float
    rv20: float
    yz20: float
    rv252: float
    rv_forecast30: float
    atm_iv30: float | None
    iv_rv_ratio30: float | None
    iv_rank: float | None
    iv_percentile: float | None
    iv_history_days: int
    contracts_quoted: int
    contracts_passing: int
    contracts_cheap: int
    notes: list[str] = field(default_factory=list)


def _clip(x, lo=-1.0, hi=1.0):
    return np.clip(x, lo, hi)


def fit_smile(x: np.ndarray, iv: np.ndarray, w: np.ndarray) -> np.ndarray | None:
    """Weighted quadratic iv = a + b*x + c*x^2 in log-moneyness x. None if too few points."""
    ok = np.isfinite(x) & np.isfinite(iv) & np.isfinite(w) & (w > 0)
    if ok.sum() < 5:
        return None
    return np.polyfit(x[ok], iv[ok], 2, w=np.sqrt(w[ok]))


def atm_iv_30(expiry_atm: pd.DataFrame, target_days: float = 30.0) -> float | None:
    """Interpolate ATM IV to 30 calendar days, linearly in total variance (iv^2 * T)."""
    df = expiry_atm.dropna().sort_values("days")
    if df.empty:
        return None
    if len(df) == 1 or target_days <= df["days"].iloc[0]:
        return float(df["atm_iv"].iloc[0])
    if target_days >= df["days"].iloc[-1]:
        return float(df["atm_iv"].iloc[-1])
    total_var = df["atm_iv"] ** 2 * df["days"]
    w = float(np.interp(target_days, df["days"], total_var))
    return math.sqrt(w / target_days)


def analyze_chain(
    symbol: str,
    chain: pd.DataFrame,
    spot: float,
    profile: VolProfile,
    params: ScanParams,
    q: float,
    today: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (all contracts with metrics, per-expiry ATM IV)."""
    df = chain.copy()
    r = params.rate
    df["days"] = (df["expiry"] - today).dt.days.clip(lower=1).astype(float)
    df["T"] = df["days"] / 365.0
    good_quote = (df["bid"] > 0) & (df["ask"] > 0) & (df["ask"] >= df["bid"])
    df["mid"] = np.where(good_quote, (df["bid"] + df["ask"]) / 2, np.nan)
    df["spread_pct"] = (df["ask"] - df["bid"]) / df["mid"]
    S, K, T, right = spot, df["strike"].to_numpy(), df["T"].to_numpy(), df["right"].to_numpy()
    df["iv_bid"] = implied_vol(df["bid"].where(good_quote).to_numpy(), S, K, T, r, q, right)
    df["iv_mid"] = implied_vol(df["mid"].to_numpy(), S, K, T, r, q, right)
    df["iv_ask"] = implied_vol(df["ask"].where(good_quote).to_numpy(), S, K, T, r, q, right)
    df["rv_forecast"] = [profile.forecast(d * 252 / 365) for d in df["days"]]
    sig = df["iv_mid"].fillna(df["rv_forecast"]).to_numpy()
    df["delta"] = bsm_delta(S, K, T, r, q, sig, right)
    df["vega"] = bsm_vega(S, K, T, r, q, sig) / 100.0  # $ per share per 1 vol point
    df["model_price"] = bsm_price(S, K, T, r, q, df["rv_forecast"].to_numpy(), right)
    df["edge_pct"] = (df["model_price"] - df["ask"]) / df["ask"]
    df["iv_rv"] = df["iv_mid"] / df["rv_forecast"]
    df["vol_gap"] = 1.0 - df["iv_ask"] / df["rv_forecast"]

    # Smile per expiry from out-of-the-money options (the liquid side of each strike).
    forward = spot * np.exp((r - q) * df["T"])
    df["log_moneyness"] = np.log(df["strike"] / forward)
    otm = ((df["right"] == "C") & (df["strike"] >= forward)) | ((df["right"] == "P") & (df["strike"] < forward))
    df["smile_iv"] = np.nan
    atm_rows = []
    for expiry, grp in df.groupby("expiry"):
        pts = grp[otm.loc[grp.index]]
        coef = fit_smile(pts["log_moneyness"].to_numpy(), pts["iv_mid"].to_numpy(), pts["vega"].to_numpy())
        if coef is None:
            continue
        df.loc[grp.index, "smile_iv"] = np.polyval(coef, grp["log_moneyness"].to_numpy())
        atm_rows.append({"expiry": expiry, "days": float(grp["days"].iloc[0]), "atm_iv": float(np.polyval(coef, 0.0))})
    df["smile_resid"] = df["iv_mid"] - df["smile_iv"]
    df.insert(0, "symbol", symbol)
    return df, pd.DataFrame(atm_rows, columns=["expiry", "days", "atm_iv"])


def score_contracts(df: pd.DataFrame, params: ScanParams, rank: float | None) -> pd.DataFrame:
    """Composite score in points (higher = cheaper), and the liquidity/delta filter."""
    df = df.copy()
    vol_pts = 45 * _clip(df["vol_gap"].fillna(-1) / 0.25)
    smile_pts = 20 * _clip(-df["smile_resid"].fillna(0) / 0.03)
    rank_pts = 0.0 if rank is None else 20 * (50 - rank) / 50
    spread_pts = -15 * np.clip(df["spread_pct"].fillna(1) / params.max_spread_pct, 0, 1)
    df["score"] = (vol_pts + smile_pts + rank_pts + spread_pts).round(1)
    df["cheap"] = df["iv_ask"] < df["rv_forecast"]
    df["passes"] = (
        df["mid"].notna()
        & df["iv_mid"].notna()
        & (df["bid"] >= params.min_bid)
        & (df["spread_pct"] <= params.max_spread_pct)
        & (df["open_interest"].fillna(0) >= params.min_open_interest)
        & (df["volume"].fillna(0) >= params.min_volume)
        & df["delta"].abs().between(params.min_abs_delta, params.max_abs_delta)
    )
    return df


class Scanner:
    def __init__(self, source, params: ScanParams | None = None, iv_store: IVStore | None = None,
                 today: pd.Timestamp | None = None):
        self.source = source
        self.params = params or ScanParams()
        self.iv_store = iv_store
        self.today = (today or pd.Timestamp.today()).normalize()

    def scan_symbol(self, symbol: str) -> tuple[SymbolSummary, pd.DataFrame]:
        p = self.params
        notes: list[str] = []
        ohlc = self.source.history(symbol, p.history_days)
        profile = vol_profile(ohlc, p.half_life_days)
        spot = self.source.spot(symbol) or float(ohlc["close"].iloc[-1])
        q = p.div_yield if p.div_yield is not None else (self.source.dividend_yield(symbol) or 0.0)
        chain = self.source.chain(symbol, p.min_dte, p.max_dte, p.lo_moneyness, p.hi_moneyness)
        if chain.empty:
            notes.append("no option quotes returned")
            contracts = pd.DataFrame()
            atm = pd.DataFrame(columns=["expiry", "days", "atm_iv"])
        else:
            contracts, atm = analyze_chain(symbol, chain, spot, profile, p, q, self.today)

        atm30 = atm_iv_30(atm)
        hist = self.source.iv_history(symbol)
        if hist is None or len(hist) < 20:
            if self.iv_store is not None:
                hist = self.iv_store.load(symbol)
        if self.iv_store is not None and atm30 is not None:
            self.iv_store.record(symbol, self.today, atm30)
        hist = pd.Series(dtype=float) if hist is None else hist
        rank, pct = iv_rank(hist, atm30) if atm30 is not None else (None, None)
        if rank is None:
            notes.append(f"IV rank needs >= 20 days of IV history (have {len(hist)})")

        if not contracts.empty:
            contracts = score_contracts(contracts, p, rank)
            if contracts["mid"].notna().sum() == 0:
                notes.append("no two-sided quotes (market closed, or no options data subscription?)")
        rv30 = profile.forecast(30 * 252 / 365)
        summary = SymbolSummary(
            symbol=symbol, spot=spot, div_yield=q, rv20=profile.rv20, yz20=profile.yz20, rv252=profile.rv252,
            rv_forecast30=rv30, atm_iv30=atm30, iv_rv_ratio30=(atm30 / rv30) if atm30 else None,
            iv_rank=rank, iv_percentile=pct, iv_history_days=len(hist),
            contracts_quoted=len(contracts),
            contracts_passing=int(contracts["passes"].sum()) if not contracts.empty else 0,
            contracts_cheap=int((contracts["passes"] & contracts["cheap"]).sum()) if not contracts.empty else 0,
            notes=notes,
        )
        return summary, contracts

    def scan(self, symbols: list[str]) -> tuple[list[SymbolSummary], pd.DataFrame]:
        summaries, frames = [], []
        for sym in symbols:
            try:
                summary, contracts = self.scan_symbol(sym)
            except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the scan
                log.warning("%s: skipped (%s)", sym, exc)
                continue
            summaries.append(summary)
            if not contracts.empty:
                frames.append(contracts)
        all_contracts = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        return summaries, all_contracts


def top_cheap(contracts: pd.DataFrame, n: int = 25, only_cheap: bool = True) -> pd.DataFrame:
    if contracts.empty:
        return contracts
    df = contracts[contracts["passes"]]
    if only_cheap:
        df = df[df["cheap"]]
    return df.sort_values("score", ascending=False).head(n).reset_index(drop=True)


def summary_dicts(summaries: list[SymbolSummary]) -> list[dict]:
    return [asdict(s) for s in summaries]
