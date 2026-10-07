"""MCP server (stdio): read-only Schwab account and market data tools, plus opt-in guarded order tools."""

from __future__ import annotations

import datetime as dt
import functools
import logging
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import formatting as fmt
from . import orders as od
from . import validation as v
from .client import SchwabClient
from .config import Settings, load_settings
from .errors import InvalidInput, SchwabError
from .oauth import TokenManager
from .tokens import make_store

log = logging.getLogger("schwab_mcp")

mcp = MCPServer(
    name="schwab",
    instructions=(
        "Access to the user's Charles Schwab accounts and market data. "
        "Account numbers are masked to the last 4 digits; use account_hash (from get_accounts) "
        "or the last 4 digits to pick an account. Order tools (place_order, replace_order, cancel_order) "
        "only work when the user enabled trading in .env. They always preview first and send only when "
        "called again with the confirm_token the preview returned; show the user the preview and get "
        "their explicit go-ahead before confirming."
    ),
)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
TRADING = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True)


class Backend:
    """Lazily built so the server starts (and reports config problems per call) without a .env."""

    def __init__(self, client: SchwabClient | None = None, settings: Settings | None = None):
        self._client = client
        self._settings = settings
        self.confirm = od.ConfirmTokens()
        self._accounts: list[dict] | None = None  # [{"accountNumber", "hashValue"}]

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = load_settings()
        return self._settings

    @property
    def client(self) -> SchwabClient:
        if self._client is None:
            self._client = SchwabClient(TokenManager(self.settings, make_store(self.settings)))
        return self._client

    def known_numbers(self) -> list[str]:
        return [str(a.get("accountNumber")) for a in self._accounts or []]

    async def account_numbers(self) -> list[dict]:
        if self._accounts is None:
            self._accounts = await self.client.get("/trader/v1/accounts/accountNumbers") or []
        return self._accounts

    async def resolve(self, account: str | None) -> str:
        """Map a hash, last-4 digits, or nothing (single account) to an account hash."""
        accounts = await self.account_numbers()
        if not accounts:
            raise InvalidInput("No accounts are linked to this Schwab login.")
        if not account:
            if len(accounts) == 1:
                return accounts[0]["hashValue"]
            listed = ", ".join(fmt.mask_account(a.get("accountNumber")) for a in accounts)
            raise InvalidInput(f"You have several accounts ({listed}); pass account= the last 4 digits or hash.")
        account = account.strip()
        for a in accounts:
            if account == a.get("hashValue"):
                return account
        digits = account.lstrip("*")
        matches = [a for a in accounts if digits.isdigit() and str(a.get("accountNumber", "")).endswith(digits)]
        if len(matches) == 1:
            return matches[0]["hashValue"]
        if len(matches) > 1:
            raise InvalidInput("More than one account ends with those digits; use the account_hash.")
        raise InvalidInput("No linked account matches that value. Call get_accounts to see valid choices.")


backend = Backend()


def tool(fn=None, *, annotations: ToolAnnotations = READ_ONLY):
    """Register a tool that turns every failure into a readable ToolError and masks output."""
    if fn is None:
        return lambda f: tool(f, annotations=annotations)

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            result = await fn(*args, **kwargs)
        except SchwabError as exc:
            raise ToolError(fmt.mask_text(str(exc))) from None
        except Exception as exc:  # never crash the server or leak internals
            log.exception("Unexpected error in %s", fn.__name__)
            raise ToolError(f"Unexpected error ({type(exc).__name__}). Check the server log.") from None
        return fmt.scrub(result, backend.known_numbers())

    return mcp.tool(annotations=annotations)(wrapper)


@tool
async def get_accounts() -> dict[str, Any]:
    """List linked Schwab accounts with masked numbers and the hash IDs other tools accept."""
    numbers = await backend.account_numbers()
    accounts = await backend.client.get("/trader/v1/accounts")
    return {"accounts": fmt.shape_account_list(numbers, accounts or [])}


@tool
async def get_account_summary(account: str | None = None, detail: bool = False) -> dict[str, Any]:
    """Balances, buying power, equity and cash.

    account: last 4 digits or account_hash; omit for all accounts.
    detail: also include Schwab's full current/initial/projected balance blocks.
    """
    if account:
        h = await backend.resolve(account)
        raw = await backend.client.get(f"/trader/v1/accounts/{h}")
        return {"accounts": [fmt.shape_account_summary(raw, h, detail)]}
    numbers = await backend.account_numbers()
    hashes = {str(a.get("accountNumber")): a.get("hashValue") for a in numbers}
    raws = await backend.client.get("/trader/v1/accounts") or []
    return {
        "accounts": [
            fmt.shape_account_summary(r, hashes.get(str(r.get("securitiesAccount", {}).get("accountNumber"))), detail)
            for r in raws
        ]
    }


@tool
async def get_positions(account: str | None = None) -> dict[str, Any]:
    """Current holdings: quantity, average price, cost basis, market value, day P/L and total P/L.

    pct_account is the position's market value as a percent of the account's total value (positions plus cash).

    account: last 4 digits or account_hash; may be omitted if you have one account.
    """
    h = await backend.resolve(account)
    raw = await backend.client.get(f"/trader/v1/accounts/{h}", {"fields": "positions"})
    return fmt.shape_positions(raw, h)


@tool
async def get_quote(symbols: list[str], include_fundamentals: bool = False) -> dict[str, Any]:
    """Real-time or delayed quotes for up to 50 symbols (e.g. ["AAPL", "$SPX", "/ES"])."""
    syms = v.symbols(symbols)
    fields = "quote,reference,fundamental" if include_fundamentals else "quote,reference"
    raw = await backend.client.get("/marketdata/v1/quotes", {"symbols": ",".join(syms), "fields": fields})
    return fmt.shape_quotes(raw or {}, syms, include_fundamentals)


@tool
async def get_price_history(
    symbol: str,
    period_type: str = "month",
    period: int | None = 1,
    frequency_type: str | None = None,
    frequency: int = 1,
    start_date: str | None = None,
    end_date: str | None = None,
    extended_hours: bool = False,
    max_candles: int = 500,
) -> dict[str, Any]:
    """OHLCV candles.

    period_type: day | month | year | ytd. period: day 1-5,10; month 1,2,3,6; year 1,2,3,5,10,15,20; ytd 1.
    frequency_type: minute (day only) | daily | weekly | monthly; defaults to the first allowed.
    frequency: minutes 1,5,10,15,30; otherwise 1.
    start_date/end_date (YYYY-MM-DD) override period. Minute data goes back roughly 48 days.
    """
    sym = v.symbol(symbol)
    pt, ft = v.price_history_params(period_type, period, frequency_type, frequency)
    v.int_range(max_candles, 1, 10000, "max_candles")
    start, end = v.date(start_date, "start_date"), v.date(end_date, "end_date")
    v.date_range(start, end)
    params: dict[str, Any] = {
        "symbol": sym,
        "periodType": pt,
        "frequencyType": ft,
        "frequency": frequency,
        "needExtendedHoursData": str(extended_hours).lower(),
    }
    if start:
        params["startDate"] = _epoch_ms(start)
    elif period is not None:
        params["period"] = period
    # Without endDate Schwab stops at the previous trading day's close, which hides today's candles.
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    params["endDate"] = min(_epoch_ms(end + dt.timedelta(days=1)) - 1, now_ms) if end else now_ms
    raw = await backend.client.get("/marketdata/v1/pricehistory", params)
    return fmt.shape_candles(raw or {}, max_candles)


@tool
async def get_option_chain(
    symbol: str,
    contract_type: str = "ALL",
    strike_count: int = 10,
    strike_range: str = "ALL",
    from_date: str | None = None,
    to_date: str | None = None,
    max_expirations: int = 3,
    full: bool = False,
) -> dict[str, Any]:
    """Option chain, trimmed by default to 10 strikes around the money and the next 3 expirations within 60 days.

    contract_type: ALL | CALL | PUT. strike_range: ALL | ITM | NTM | OTM | SAK | SBK | SNK.
    from_date/to_date: expiration window (YYYY-MM-DD). full=true returns every field per contract.
    """
    sym = v.symbol(symbol)
    ct = v.choice(contract_type, v.CONTRACT_TYPES, "contract_type")
    sr = v.choice(strike_range, v.STRIKE_RANGES, "strike_range")
    v.int_range(strike_count, 1, 200, "strike_count")
    v.int_range(max_expirations, 1, 60, "max_expirations")
    today = dt.date.today()
    start = v.date(from_date, "from_date") or today
    end = v.date(to_date, "to_date") or (start + dt.timedelta(days=60))
    v.date_range(start, end, allow_future=True)
    params = {
        "symbol": sym,
        "contractType": ct,
        "strikeCount": strike_count,
        "range": sr,
        "fromDate": start.isoformat(),
        "toDate": end.isoformat(),
        "includeUnderlyingQuote": "false",
    }
    raw = await backend.client.get("/marketdata/v1/chains", params)
    if isinstance(raw, dict) and raw.get("status") == "FAILED":
        raise InvalidInput(f"Schwab has no option chain for {sym} in that window.")
    return fmt.shape_option_chain(raw or {}, max_expirations, full)


@tool
async def get_market_hours(markets: list[str] | None = None, date: str | None = None) -> dict[str, Any]:
    """Market hours and whether the regular session is open now.

    markets: any of equity, option, bond, future, forex (default equity, option). date: YYYY-MM-DD, up to a year ahead.
    """
    chosen = [v.choice(m, v.MARKETS, "markets", upper=False) for m in (markets or ["equity", "option"])]
    params: dict[str, Any] = {"markets": ",".join(dict.fromkeys(chosen))}
    d = v.date(date, "date")
    if d:
        if d > dt.date.today() + dt.timedelta(days=365):
            raise InvalidInput("date can be at most one year ahead.")
        params["date"] = d.isoformat()
    raw = await backend.client.get("/marketdata/v1/markets", params)
    return fmt.shape_market_hours(raw or {})


# --- Orders -----------------------------------------------------------------------------------------------
# get_orders is read-only. The three order tools are disabled unless SCHWAB_TRADING_ENABLED=true and always
# work in two steps: a preview that returns a confirm_token, then the same call again with that token.


def _require_trading() -> None:
    if not backend.settings.trading_enabled:
        raise InvalidInput(
            "Trading is disabled. To enable the order tools, set SCHWAB_TRADING_ENABLED=true in your .env "
            "and restart the server."
        )


def _order_id(value: str) -> str:
    oid = str(value or "").strip()
    if not oid.isdigit() or len(oid) > 20:
        raise InvalidInput("order_id must be the numeric order id from get_orders.")
    return oid


async def _quote_price(spec: dict[str, Any]) -> float | None:
    """Ask/last price for sizing a market order; None when the spec already has a price."""
    if od.unit_price(spec) is not None:
        return None
    sym = od.leg(spec)["instrument"]["symbol"]
    raw = await backend.client.get("/marketdata/v1/quotes", {"symbols": sym, "fields": "quote"})
    q = ((raw or {}).get(sym) or {}).get("quote") or {}
    prices = [q.get(k) for k in ("askPrice", "lastPrice", "mark") if isinstance(q.get(k), (int, float))]
    return max(prices) if prices else None


async def _two_step(
    kind: str, account: str | None, order_id: str | None, spec: dict[str, Any] | None, confirm_token: str | None
) -> dict[str, Any]:
    h = await backend.resolve(account)
    current = None
    if order_id:
        current = od.shape_order(await backend.client.get_order(h, order_id))
    value = None
    if spec is not None:
        value = od.estimate_value(spec, await _quote_price(spec))
        od.check_cap(value, backend.settings.max_order_value)
    fingerprint = backend.confirm.fingerprint(kind, h, order_id, spec)
    if not confirm_token:
        out: dict[str, Any] = {
            "status": "PREVIEW - nothing was sent",
            "action": kind,
            "account": fmt.mask_account(
                next((a.get("accountNumber") for a in await backend.account_numbers() if a.get("hashValue") == h), "")
            ),
        }
        if spec is not None:
            out["order"] = od.describe(spec)
            out["estimated_value"] = float(value)
            out["max_order_value"] = backend.settings.max_order_value
        if current is not None:
            out["existing_order"] = current
        out["confirm_token"] = backend.confirm.issue(fingerprint)
        out["expires_in_seconds"] = od.TOKEN_TTL
        out["next"] = "To send, call the same tool again with identical arguments plus this confirm_token."
        return out
    backend.confirm.redeem(confirm_token, fingerprint)
    method = {"place": "POST", "replace": "PUT", "cancel": "DELETE"}[kind]
    log.warning("Sending %s order request", kind)
    result = await backend.client.send_order(method, h, order_id, spec)
    return {"status": {"place": "SENT", "replace": "REPLACE SENT", "cancel": "CANCEL SENT"}[kind], **result,
            "next": "Call get_orders to confirm the order's status."}


@tool
async def get_orders(
    account: str | None = None, status: str | None = None, days: int = 7, max_results: int = 50
) -> dict[str, Any]:
    """Recent orders (read-only): order_id, status, type, quantity, filled, price, legs.

    account: last 4 digits or account_hash. status: optional, e.g. WORKING, FILLED, CANCELED, REJECTED, QUEUED.
    days: how far back to look (1-60). Use this to find the order_id that replace_order and cancel_order need.
    """
    v.int_range(days, 1, 60, "days")
    v.int_range(max_results, 1, 200, "max_results")
    h = await backend.resolve(account)
    now = dt.datetime.now(dt.timezone.utc)
    fmt_t = "%Y-%m-%dT%H:%M:%S.000Z"
    params: dict[str, Any] = {
        "fromEnteredTime": (now - dt.timedelta(days=days)).strftime(fmt_t),
        "toEnteredTime": now.strftime(fmt_t),
        "maxResults": max_results,
    }
    if status:
        params["status"] = v.choice(status, v.ORDER_STATUSES, "status")
    raw = await backend.client.get_orders(h, params)
    return {"orders": [od.shape_order(o) for o in raw or []][:max_results]}


@tool(annotations=TRADING)
async def place_order(
    symbol: str,
    instruction: str,
    quantity: int,
    order_type: str,
    asset_type: str = "EQUITY",
    limit_price: float | None = None,
    stop_price: float | None = None,
    duration: str = "DAY",
    session: str = "NORMAL",
    account: str | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Place a single-leg equity or option order. TWO STEPS: without confirm_token it only previews.

    Show the preview to the user; only after they explicitly agree, call again with the same arguments
    plus the confirm_token to send it. Requires SCHWAB_TRADING_ENABLED=true.

    instruction: equities BUY | SELL | SELL_SHORT | BUY_TO_COVER; options BUY_TO_OPEN | BUY_TO_CLOSE |
    SELL_TO_OPEN | SELL_TO_CLOSE. order_type: MARKET | LIMIT | STOP | STOP_LIMIT (limit_price / stop_price
    as required). duration: DAY | GTC. session: NORMAL | AM | PM | SEAMLESS. Option symbols are OCC format
    like 'AAPL  250117C00150000'. Orders over SCHWAB_MAX_ORDER_VALUE are refused.
    """
    _require_trading()
    spec = od.build_spec(symbol, instruction, quantity, order_type, asset_type, limit_price, stop_price, duration, session)
    return await _two_step("place", account, None, spec, confirm_token)


@tool(annotations=TRADING)
async def replace_order(
    order_id: str,
    symbol: str,
    instruction: str,
    quantity: int,
    order_type: str,
    asset_type: str = "EQUITY",
    limit_price: float | None = None,
    stop_price: float | None = None,
    duration: str = "DAY",
    session: str = "NORMAL",
    account: str | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Replace a working order with a complete new order spec (Schwab gives it a new order id).

    TWO STEPS: without confirm_token it previews, showing the existing order next to the new one. Same
    arguments as place_order plus order_id (from get_orders). Requires SCHWAB_TRADING_ENABLED=true.
    """
    _require_trading()
    oid = _order_id(order_id)
    spec = od.build_spec(symbol, instruction, quantity, order_type, asset_type, limit_price, stop_price, duration, session)
    return await _two_step("replace", account, oid, spec, confirm_token)


@tool(annotations=TRADING)
async def cancel_order(order_id: str, account: str | None = None, confirm_token: str | None = None) -> dict[str, Any]:
    """Cancel a working order. TWO STEPS: without confirm_token it previews the order that would be canceled.

    order_id comes from get_orders. Requires SCHWAB_TRADING_ENABLED=true.
    """
    _require_trading()
    return await _two_step("cancel", account, _order_id(order_id), None, confirm_token)


def _epoch_ms(d: dt.date) -> int:
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000)


def main() -> None:
    # stdout is the MCP channel: logs go to stderr only, and never at a level that prints request details.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.WARNING)
    mcp.run("stdio")
