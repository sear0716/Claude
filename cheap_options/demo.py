"""Synthetic snapshots so the scanner can be tried, and tested, with no network or account."""

from __future__ import annotations

import datetime as dt
import math
import random

from . import pricing
from .models import Bar, OptionQuote, Snapshot


def make_snapshot(symbol: str, realised_vol: float, implied_vol: float, seed: int = 1, spot: float = 100.0,
                  asof: dt.date = dt.date(2026, 1, 5), dtes: tuple[int, ...] = (18, 46, 74),
                  rate: float = 0.04, dislocate: tuple[int, float, str, float] | None = None,
                  spread: float = 0.04) -> Snapshot:
    """Price history with the given realised vol and a chain priced at the given ATM implied vol.

    dislocate=(dte, strike, right, vol_shift) moves one contract's vol off the smile.
    """
    rng = random.Random(seed)
    daily = realised_vol / math.sqrt(252)
    closes = [spot]
    for _ in range(800):
        closes.append(closes[-1] * math.exp(rng.gauss(-0.5 * daily * daily, daily)))
    scale = spot / closes[-1]
    bars, day = [], asof - dt.timedelta(days=len(closes) - 1)
    for prev, close in zip(closes, closes[1:]):
        day += dt.timedelta(days=1)
        o, c = prev * scale, close * scale
        wiggle = abs(rng.gauss(0, daily * 0.5))
        bars.append(Bar(day, o, max(o, c) * (1 + wiggle), min(o, c) * (1 - wiggle), c))
    quotes = []
    for dte in dtes:
        t = dte / 365.0
        df = math.exp(-rate * t)
        fwd = spot / df
        expiry = asof + dt.timedelta(days=dte)
        for i in range(-12, 13):
            strike = round(spot * (1 + 0.025 * i), 2)
            x = math.log(strike / fwd) / (implied_vol * math.sqrt(t))
            base = implied_vol * (1 - 0.06 * x + 0.03 * x * x)  # put skew plus a little curvature
            for right in "CP":
                iv = base
                if dislocate and dislocate[:3] == (dte, strike, right):
                    iv = base + dislocate[3]
                price = pricing.black76(fwd, strike, iv, t, df, right)
                bid, ask = round(price * (1 - spread / 2), 2), round(price * (1 + spread / 2), 2)
                if bid > 0:
                    quotes.append(OptionQuote(expiry, strike, right, bid, ask, 500, 2000,
                                              f"{symbol}{expiry:%y%m%d}{right}{int(strike * 1000):08d}"))
    return Snapshot(symbol, asof, spot, bars, quotes)


def demo_snapshots() -> list[Snapshot]:
    return [
        make_snapshot("QUIET", realised_vol=0.34, implied_vol=0.25, seed=11),  # options cheap vs movement
        make_snapshot("FAIR", realised_vol=0.24, implied_vol=0.27, seed=12,
                      dislocate=(46, 105.0, "C", -0.04)),  # one strike sitting below its smile
        make_snapshot("PRICEY", realised_vol=0.20, implied_vol=0.40, seed=13),  # options rich
    ]
