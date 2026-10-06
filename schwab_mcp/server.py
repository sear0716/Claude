"""MCP server (stdio) exposing read-only Schwab account and market data tools."""

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
from . import validation as v
from .client import SchwabClient
from .config import load_settings
from .errors import InvalidInput, SchwabError
from .oauth import TokenManager
from .tokens import make_store

log = logging.getLogger("schwab_mcp")

mcp = MCPServer(
    name="schwab",
    instructions=(
        "Read-only access to the user's Charles Schwab accounts and market data. "
        "Account numbers are masked to the last 4 digits; use account_hash (from get_accounts) "
        "or the last 4 digits to pick an account. No trading is possible through this server."
    ),
)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)


class Backend:
    """Lazily built so the server starts (and reports config problems per call) without a .env."""

    def __init__(self, client: SchwabClient | None = None):
        self._client = client
        self._accounts: list[dict] | None = None  # [{"accountNumber", "hashValue"}]

    @property
    def client(self) -> SchwabClient:
        if self._client is None:
            settings = load_settings()
            self._client = SchwabClient(TokenManager(settings, make_store(settings)))
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


def tool(fn):
    """Register a read-only tool that turns every failure into a readable ToolError and masks output."""

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

    return mcp.tool(annotations=READ_ONLY)(wrapper)


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
    if start or end:
        if start:
            params["startDate"] = _epoch_ms(start)
        if end:
            params["endDate"] = _epoch_ms(end + dt.timedelta(days=1)) - 1
    elif period is not None:
        params["period"] = period
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


def _epoch_ms(d: dt.date) -> int:
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000)


def main() -> None:
    # stdout is the MCP channel: logs go to stderr only, and never at a level that prints request details.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.WARNING)
    mcp.run("stdio")
