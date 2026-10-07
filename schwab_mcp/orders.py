"""Order validation, spec building, value estimation and the preview/confirm token.

Nothing here talks to the network. Sending happens only in client.send_order.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from . import validation as v
from .errors import InvalidInput

ORDER_TYPES = ("MARKET", "LIMIT", "STOP", "STOP_LIMIT")
DURATIONS = ("DAY", "GTC")
SESSIONS = ("NORMAL", "AM", "PM", "SEAMLESS")
ASSET_TYPES = ("EQUITY", "OPTION")
INSTRUCTIONS = {
    "EQUITY": ("BUY", "SELL", "SELL_SHORT", "BUY_TO_COVER"),
    "OPTION": ("BUY_TO_OPEN", "BUY_TO_CLOSE", "SELL_TO_OPEN", "SELL_TO_CLOSE"),
}
OCC_RE = re.compile(r"^([A-Z.]{1,6})\s*(\d{6}[CP]\d{8})$")
TOKEN_TTL = 300  # seconds a preview stays confirmable


def _price(value: float | str | None, name: str) -> str | None:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise InvalidInput(f"{name} must be a number.") from None
    if not d.is_finite() or d <= 0 or d > Decimal("1000000"):
        raise InvalidInput(f"{name} must be a positive price.")
    if d.as_tuple().exponent < -4:
        raise InvalidInput(f"{name} can have at most 4 decimal places.")
    return format(d.normalize(), "f")


def build_spec(
    symbol: str,
    instruction: str,
    quantity: int,
    order_type: str,
    asset_type: str = "EQUITY",
    limit_price: float | None = None,
    stop_price: float | None = None,
    duration: str = "DAY",
    session: str = "NORMAL",
) -> dict[str, Any]:
    """Validate everything and return the Schwab order JSON. Raises InvalidInput on any problem."""
    asset = v.choice(asset_type, ASSET_TYPES, "asset_type")
    instr = v.choice(instruction, INSTRUCTIONS[asset], "instruction")
    otype = v.choice(order_type, ORDER_TYPES, "order_type")
    dur = v.choice(duration, DURATIONS, "duration")
    sess = v.choice(session, SESSIONS, "session")
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise InvalidInput("quantity must be a whole number.")
    v.int_range(quantity, 1, 1_000_000, "quantity")
    sym = v.symbol(symbol)
    if asset == "OPTION":
        m = OCC_RE.match(sym)
        if not m:
            raise InvalidInput("Option symbols must be OCC format, e.g. 'AAPL  250117C00150000'.")
        sym = m.group(1).ljust(6) + m.group(2)
    elif not re.match(r"^[A-Z][A-Z0-9.]{0,9}$", sym):
        raise InvalidInput("Equity orders accept plain tickers only (no indexes or futures).")
    limit, stop = _price(limit_price, "limit_price"), _price(stop_price, "stop_price")
    if otype in ("LIMIT", "STOP_LIMIT") and limit is None:
        raise InvalidInput(f"{otype} orders need limit_price.")
    if otype in ("STOP", "STOP_LIMIT") and stop is None:
        raise InvalidInput(f"{otype} orders need stop_price.")
    if limit is not None and otype not in ("LIMIT", "STOP_LIMIT"):
        raise InvalidInput(f"limit_price is not used for {otype} orders.")
    if stop is not None and otype not in ("STOP", "STOP_LIMIT"):
        raise InvalidInput(f"stop_price is not used for {otype} orders.")
    if otype == "MARKET" and (dur != "DAY" or sess != "NORMAL"):
        raise InvalidInput("Market orders must be DAY orders in the NORMAL session.")
    spec: dict[str, Any] = {
        "orderType": otype,
        "session": sess,
        "duration": "GOOD_TILL_CANCEL" if dur == "GTC" else "DAY",
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {"instruction": instr, "quantity": quantity, "instrument": {"symbol": sym, "assetType": asset}}
        ],
    }
    if limit is not None:
        spec["price"] = limit
    if stop is not None:
        spec["stopPrice"] = stop
    return spec


def leg(spec: dict[str, Any]) -> dict[str, Any]:
    return spec["orderLegCollection"][0]


def unit_price(spec: dict[str, Any]) -> Decimal | None:
    """Per-share price known from the spec itself (limit, else stop); None for plain market orders."""
    for key in ("price", "stopPrice"):
        if key in spec:
            return Decimal(spec[key])
    return None


def estimate_value(spec: dict[str, Any], quote_price: float | None) -> Decimal:
    """Estimated order value in USD (options x100). A market order needs a quote price."""
    price = unit_price(spec)
    if price is None:
        if not quote_price or quote_price <= 0:
            raise InvalidInput("Could not get a price to estimate this market order's value. Use a limit order.")
        price = Decimal(str(quote_price))
    mult = 100 if leg(spec)["instrument"]["assetType"] == "OPTION" else 1
    return (price * leg(spec)["quantity"] * mult).quantize(Decimal("0.01"))


def check_cap(value: Decimal, cap: float) -> None:
    if value > Decimal(str(cap)):
        raise InvalidInput(
            f"Estimated order value ${value:,.2f} is over your SCHWAB_MAX_ORDER_VALUE cap of ${cap:,.2f}. "
            "Raise the cap in .env if you really mean it."
        )


def describe(spec: dict[str, Any]) -> str:
    l = leg(spec)
    parts = [l["instruction"], str(l["quantity"]), l["instrument"]["symbol"].strip(), spec["orderType"]]
    if "price" in spec:
        parts.append(f"limit {spec['price']}")
    if "stopPrice" in spec:
        parts.append(f"stop {spec['stopPrice']}")
    parts.append(f"{spec['duration']} / {spec['session']}")
    return " ".join(parts)


def shape_order(raw: dict[str, Any]) -> dict[str, Any]:
    """Compact view of a Schwab order. Leaves out the account number."""
    legs = [
        {
            "instruction": x.get("instruction"),
            "symbol": (x.get("instrument") or {}).get("symbol"),
            "asset_type": (x.get("instrument") or {}).get("assetType"),
            "quantity": x.get("quantity"),
        }
        for x in raw.get("orderLegCollection") or []
    ]
    out = {
        "order_id": str(raw.get("orderId")) if raw.get("orderId") is not None else None,
        "status": raw.get("status"),
        "entered": raw.get("enteredTime"),
        "type": raw.get("orderType"),
        "duration": raw.get("duration"),
        "session": raw.get("session"),
        "quantity": raw.get("quantity"),
        "filled": raw.get("filledQuantity"),
        "price": raw.get("price"),
        "stop_price": raw.get("stopPrice"),
        "legs": legs,
    }
    return {k: val for k, val in out.items() if val is not None}


class ConfirmTokens:
    """Single-use, short-lived tokens that bind a preview to the exact request that is later sent."""

    def __init__(self, ttl: int = TOKEN_TTL, clock=time.time):
        self.ttl = ttl
        self.clock = clock
        self._pending: dict[str, tuple[float, str]] = {}

    @staticmethod
    def fingerprint(kind: str, account_hash: str, order_id: str | None, spec: dict[str, Any] | None) -> str:
        return json.dumps([kind, account_hash, order_id, spec], sort_keys=True)

    def issue(self, fingerprint: str) -> str:
        now = self.clock()
        self._pending = {t: (exp, f) for t, (exp, f) in self._pending.items() if exp > now}
        token = secrets.token_urlsafe(12)
        self._pending[token] = (now + self.ttl, fingerprint)
        return token

    def redeem(self, token: str, fingerprint: str) -> None:
        entry = self._pending.pop((token or "").strip(), None)  # single use, even if it then fails
        if entry is None or entry[0] <= self.clock():
            raise InvalidInput("That confirm_token is unknown, expired or already used. Run a new preview.")
        if entry[1] != fingerprint:
            raise InvalidInput("The arguments differ from the previewed order. Run a new preview.")
