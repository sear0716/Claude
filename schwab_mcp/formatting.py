"""Masking and compact shaping of Schwab responses."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Iterable

ACCOUNT_KEYS = {"accountNumber", "account_number", "accountId"}
_LONG_DIGITS = re.compile(r"(?<!\d)\d{6,12}(?!\d)")


def mask_account(number: Any) -> str:
    s = str(number or "")
    return f"****{s[-4:]}" if s else ""


def mask_text(text: str) -> str:
    """Mask anything in free text that looks like an account number."""
    return _LONG_DIGITS.sub(lambda m: mask_account(m.group(0)), text)


def scrub(obj: Any, known_numbers: Iterable[str] = ()) -> Any:
    """Final pass over tool output: mask account-number fields and any known full account number."""
    known = [n for n in known_numbers if n]

    def walk(o: Any, key: str | None = None) -> Any:
        if isinstance(o, dict):
            return {k: walk(v, k) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        if key in ACCOUNT_KEYS and isinstance(o, (str, int)):
            return mask_account(o)
        if isinstance(o, str):
            for n in known:
                if n in o:
                    o = o.replace(n, mask_account(n))
        return o

    return walk(obj)


def _num(v: Any) -> Any:
    """Round floats for compact output; pass everything else through."""
    if isinstance(v, float):
        return round(v, 4)
    return v


def compact(d: dict[str, Any]) -> dict[str, Any]:
    """Drop None values and round floats."""
    return {k: _num(v) for k, v in d.items() if v is not None}


def ms_to_iso(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


# ---- accounts -------------------------------------------------------------

def shape_account_list(numbers: list[dict], accounts: list[dict]) -> list[dict]:
    types = {}
    for a in accounts or []:
        sa = a.get("securitiesAccount", {})
        types[str(sa.get("accountNumber"))] = sa.get("type")
    return [
        compact(
            {
                "account": mask_account(n.get("accountNumber")),
                "account_hash": n.get("hashValue"),
                "type": types.get(str(n.get("accountNumber"))),
            }
        )
        for n in numbers
    ]


SUMMARY_FIELDS = {
    # output name: Schwab currentBalances field(s), first present wins
    "liquidation_value": ("liquidationValue",),
    "equity": ("equity",),
    "cash_balance": ("cashBalance", "totalCash"),
    "cash_available_for_trading": ("cashAvailableForTrading", "availableFunds"),
    "cash_available_for_withdrawal": ("cashAvailableForWithdrawal",),
    "buying_power": ("buyingPower", "cashAvailableForTrading"),
    "day_trading_buying_power": ("dayTradingBuyingPower",),
    "long_market_value": ("longMarketValue",),
    "short_market_value": ("shortMarketValue",),
    "money_market_fund": ("moneyMarketFund",),
    "margin_balance": ("marginBalance",),
    "maintenance_requirement": ("maintenanceRequirement",),
    "unsettled_cash": ("unsettledCash",),
}


def _first(d: dict, keys: tuple[str, ...]) -> Any:
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


def shape_account_summary(raw: dict, account_hash: str | None, detail: bool) -> dict:
    sa = raw.get("securitiesAccount", {})
    current = sa.get("currentBalances", {}) or {}
    out = {
        "account": mask_account(sa.get("accountNumber")),
        "account_hash": account_hash,
        "type": sa.get("type"),
        "is_day_trader": sa.get("isDayTrader"),
        "round_trips": sa.get("roundTrips"),
    }
    out.update({name: _first(current, keys) for name, keys in SUMMARY_FIELDS.items()})
    agg = raw.get("aggregatedBalance") or {}
    out["aggregated_liquidation_value"] = agg.get("liquidationValue")
    result = compact(out)
    if detail:
        result["current_balances"] = current
        result["initial_balances"] = sa.get("initialBalances", {})
        result["projected_balances"] = sa.get("projectedBalances", {})
    return result


def shape_position(p: dict) -> dict:
    inst = p.get("instrument", {}) or {}
    long_q = p.get("longQuantity") or 0
    short_q = p.get("shortQuantity") or 0
    qty = long_q - short_q
    mv = p.get("marketValue")
    open_pl = (p.get("longOpenProfitLoss") or 0) + (p.get("shortOpenProfitLoss") or 0)
    has_open_pl = p.get("longOpenProfitLoss") is not None or p.get("shortOpenProfitLoss") is not None
    cost_basis = (mv - open_pl) if (mv is not None and has_open_pl) else None
    day_pl = p.get("currentDayProfitLoss")
    return compact(
        {
            "symbol": inst.get("symbol"),
            "description": inst.get("description"),
            "asset_type": inst.get("assetType"),
            "quantity": qty,
            "average_price": p.get("averagePrice"),
            "cost_basis": cost_basis,
            "market_value": mv,
            "day_pl": day_pl,
            "day_pl_pct": p.get("currentDayProfitLossPercentage"),
            "total_pl": open_pl if has_open_pl else None,
            "total_pl_pct": round(open_pl / abs(cost_basis) * 100, 2) if cost_basis else None,
        }
    )


def shape_positions(raw: dict, account_hash: str | None) -> dict:
    sa = raw.get("securitiesAccount", {})
    positions = [shape_position(p) for p in sa.get("positions", []) or []]
    positions.sort(key=lambda p: -abs(p.get("market_value") or 0))
    totals = {
        "market_value": sum(p.get("market_value") or 0 for p in positions),
        "day_pl": sum(p.get("day_pl") or 0 for p in positions),
        "total_pl": sum(p.get("total_pl") or 0 for p in positions),
    }
    return {
        "account": mask_account(sa.get("accountNumber")),
        "account_hash": account_hash,
        "count": len(positions),
        "totals": compact(totals),
        "positions": positions,
    }


# ---- market data ----------------------------------------------------------

def shape_quote(sym: str, q: dict, include_fundamentals: bool) -> dict:
    quote = q.get("quote", {}) or {}
    ref = q.get("reference", {}) or {}
    out = compact(
        {
            "symbol": q.get("symbol", sym),
            "description": ref.get("description"),
            "asset_type": q.get("assetMainType"),
            "realtime": q.get("realtime"),
            "last": quote.get("lastPrice"),
            "bid": quote.get("bidPrice"),
            "ask": quote.get("askPrice"),
            "mark": quote.get("mark"),
            "open": quote.get("openPrice"),
            "high": quote.get("highPrice"),
            "low": quote.get("lowPrice"),
            "prev_close": quote.get("closePrice"),
            "net_change": quote.get("netChange"),
            "net_change_pct": quote.get("netPercentChange"),
            "volume": quote.get("totalVolume"),
            "week52_high": quote.get("52WeekHigh"),
            "week52_low": quote.get("52WeekLow"),
            "quote_time": ms_to_iso(quote.get("quoteTime")),
            "trade_time": ms_to_iso(quote.get("tradeTime")),
        }
    )
    if include_fundamentals and q.get("fundamental"):
        f = q["fundamental"]
        out["fundamentals"] = compact(
            {
                "pe_ratio": f.get("peRatio"),
                "eps": f.get("eps"),
                "div_yield": f.get("divYield"),
                "div_amount": f.get("divAmount"),
                "next_div_date": f.get("nextDivExDate") or f.get("divExDate"),
                "avg_volume_10d": f.get("avg10DaysVolume"),
                "avg_volume_1y": f.get("avg1YearVolume"),
            }
        )
    return out


def shape_quotes(raw: dict, requested: list[str], include_fundamentals: bool) -> dict:
    quotes = []
    for sym, q in raw.items():
        if sym == "errors" or not isinstance(q, dict):
            continue
        quotes.append(shape_quote(sym, q, include_fundamentals))
    errors = raw.get("errors") or {}
    invalid = list(errors.get("invalidSymbols", []) or []) if isinstance(errors, dict) else []
    found = {q["symbol"] for q in quotes}
    invalid += [s for s in requested if s not in found and s not in invalid]
    out: dict[str, Any] = {"quotes": quotes}
    if invalid:
        out["not_found"] = invalid
    return out


def shape_candles(raw: dict, max_candles: int) -> dict:
    candles = raw.get("candles", []) or []
    truncated = len(candles) > max_candles
    if truncated:
        candles = candles[-max_candles:]
    rows = [
        [ms_to_iso(c.get("datetime")), _num(c.get("open")), _num(c.get("high")), _num(c.get("low")),
         _num(c.get("close")), c.get("volume")]
        for c in candles
    ]
    out = {
        "symbol": raw.get("symbol"),
        "columns": ["time", "open", "high", "low", "close", "volume"],
        "count": len(rows),
        "candles": rows,
    }
    if truncated:
        out["truncated"] = f"Showing the most recent {max_candles} candles; raise max_candles for more."
    if raw.get("previousClose") is not None:
        out["previous_close"] = raw.get("previousClose")
    return out


OPTION_COLUMNS = ["strike", "bid", "ask", "last", "mark", "volume", "open_interest", "iv",
                  "delta", "gamma", "theta", "vega", "itm"]


def _contract_row(c: dict) -> list:
    return [_num(c.get(k)) for k in ("strikePrice", "bid", "ask", "last", "mark", "totalVolume",
                                     "openInterest", "volatility", "delta", "gamma", "theta", "vega",
                                     "inTheMoney")]


def _contract_full(c: dict) -> dict:
    return compact(
        {
            "symbol": c.get("symbol"),
            "description": c.get("description"),
            **{col: v for col, v in zip(OPTION_COLUMNS, _contract_row(c))},
            "rho": c.get("rho"),
            "bid_size": c.get("bidSize"),
            "ask_size": c.get("askSize"),
            "theoretical_value": c.get("theoreticalOptionValue"),
            "time_value": c.get("timeValue"),
            "multiplier": c.get("multiplier"),
        }
    )


def shape_option_chain(raw: dict, max_expirations: int, full: bool) -> dict:
    expirations: dict[str, dict] = {}
    for side, key in (("calls", "callExpDateMap"), ("puts", "putExpDateMap")):
        for exp_key, strikes in (raw.get(key) or {}).items():
            date, _, dte = exp_key.partition(":")
            entry = expirations.setdefault(date, {"date": date, "dte": int(dte) if dte.isdigit() else None,
                                                  "calls": [], "puts": []})
            for _strike, contracts in sorted(strikes.items(), key=lambda kv: float(kv[0])):
                for c in contracts:
                    entry[side].append(_contract_full(c) if full else _contract_row(c))
    ordered = [expirations[d] for d in sorted(expirations)]
    out: dict[str, Any] = {
        "symbol": raw.get("symbol"),
        "underlying_price": raw.get("underlyingPrice"),
        "is_delayed": raw.get("isDelayed"),
        "total_expirations": len(ordered),
    }
    if not full:
        out["columns"] = OPTION_COLUMNS
    out["expirations"] = [compact(e) for e in ordered[:max_expirations]]
    if len(ordered) > max_expirations:
        out["truncated"] = (
            f"Showing {max_expirations} of {len(ordered)} expirations; raise max_expirations or narrow the dates."
        )
    return out


def shape_market_hours(raw: dict, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    markets = []
    for market, products in (raw or {}).items():
        for product, info in (products or {}).items():
            sessions = {}
            open_now = False
            for name, windows in (info.get("sessionHours") or {}).items():
                sessions[name] = [[w.get("start"), w.get("end")] for w in windows or []]
                if name == "regularMarket":
                    for w in windows or []:
                        try:
                            start = dt.datetime.fromisoformat(w["start"])
                            end = dt.datetime.fromisoformat(w["end"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        if start <= now < end:
                            open_now = True
            markets.append(
                compact(
                    {
                        "market": market,
                        "product": info.get("product", product),
                        "product_name": info.get("productName"),
                        "date": info.get("date"),
                        "is_trading_day": info.get("isOpen"),
                        "regular_session_open_now": open_now if info.get("isOpen") else False,
                        "sessions": sessions or None,
                    }
                )
            )
    return {"as_of": now.isoformat(), "markets": markets}
