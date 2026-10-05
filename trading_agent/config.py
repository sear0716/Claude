"""Runtime configuration, read from environment variables with sane defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else default


@dataclass
class Config:
    # Interactive Brokers (TWS or IB Gateway must be running locally).
    # Paper TWS = 7497, live TWS = 7496, paper Gateway = 4002, live Gateway = 4001.
    ib_host: str = field(default_factory=lambda: os.environ.get("IB_HOST", "127.0.0.1"))
    ib_port: int = field(default_factory=lambda: _env_int("IB_PORT", 7497))
    ib_client_id: int = field(default_factory=lambda: _env_int("IB_CLIENT_ID", 17))
    ib_account: str | None = field(default_factory=lambda: os.environ.get("IB_ACCOUNT") or None)
    # Startup connect retries: wait backoff, 2x backoff, ... seconds between attempts.
    ib_connect_attempts: int = field(default_factory=lambda: _env_int("IB_CONNECT_ATTEMPTS", 3))
    ib_connect_backoff: float = field(default_factory=lambda: _env_float("IB_CONNECT_BACKOFF", 2.0))
    # IB asks clients to space out historical-data requests (pacing limits).
    ib_request_pause: float = field(default_factory=lambda: _env_float("IB_REQUEST_PAUSE", 0.5))

    # SEC requires a descriptive User-Agent with contact info on every request.
    sec_user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "SEC_USER_AGENT", "trading-agent research contact@example.com"
        )
    )
    # Optional: without a key the agent falls back to FRED's public CSV endpoint.
    fred_api_key: str | None = field(default_factory=lambda: os.environ.get("FRED_API_KEY") or None)

    cache_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("TRADING_AGENT_CACHE", Path.home() / ".cache" / "trading_agent")
        )
    )

    # Strategy parameters.
    sma_fast: int = 50
    sma_slow: int = 200
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    atr_period: int = 14
    atr_stop_mult: float = 2.0  # initial stop = entry - 2 * ATR
    atr_trail_mult: float = 3.0  # chandelier trailing stop = 22d high - 3 * ATR
    trail_lookback: int = 22
    reward_risk: float = 2.0  # first target = entry + 2R

    # Risk / sizing.
    risk_per_trade: float = field(default_factory=lambda: _env_float("RISK_PER_TRADE", 0.01))
    max_position_pct: float = field(default_factory=lambda: _env_float("MAX_POSITION_PCT", 0.10))
    default_equity: float = field(default_factory=lambda: _env_float("ACCOUNT_EQUITY", 100_000.0))

    # Selection.
    top_n: int = 10
    per_sector: int = 10
    max_per_sector_in_top: int = 3
    min_buy_score: float = 40.0
    history_days: int = 400  # ~1.6 trading years: enough to warm up the 200-day SMA
