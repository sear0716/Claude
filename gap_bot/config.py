"""Settings from .env plus strategy rules from rules.json, with the paper-only guard."""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PKG_DIR = Path(__file__).resolve().parent
LIVE_PORTS = {7496, 4001}   # TWS live, IB Gateway live
PAPER_PORTS = {7497, 4002}  # TWS paper, IB Gateway paper


class PaperOnlyError(RuntimeError):
    """Raised whenever the configuration could reach a live account."""


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 21
    paper_trading: bool = True
    portfolio_value: float = 25_000.0
    max_trade_size: float = 2_500.0
    telegram_token: str = ""
    telegram_chat_id: str = ""
    ntfy_topic: str = ""
    rules: dict = field(default_factory=dict)
    state_dir: Path = PKG_DIR / "state"
    log_dir: Path = PKG_DIR / "logs"


def load_rules(path=None):
    with open(path or PKG_DIR / "rules.json") as f:
        return json.load(f)


def load_settings(env_file=None, rules_file=None):
    load_dotenv(env_file or PKG_DIR / ".env")
    env = os.environ.get
    s = Settings(
        host=env("IBKR_HOST", "127.0.0.1"),
        port=int(env("IBKR_PORT", "7497")),
        client_id=int(env("IBKR_CLIENT_ID", "21")),
        paper_trading=env("PAPER_TRADING", "true").strip().lower() == "true",
        portfolio_value=float(env("PORTFOLIO_VALUE_USD", "25000")),
        max_trade_size=float(env("MAX_TRADE_SIZE_USD", "2500")),
        telegram_token=env("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=env("TELEGRAM_CHAT_ID", ""),
        ntfy_topic=env("NTFY_TOPIC", ""),
        rules=load_rules(rules_file),
    )
    check_paper_only(s)
    return s


def check_paper_only(s):
    """This bot is paper-only. Anything that looks live aborts before connecting."""
    if not s.paper_trading:
        raise PaperOnlyError("PAPER_TRADING must be true: this bot only trades paper accounts.")
    if s.port in LIVE_PORTS:
        raise PaperOnlyError(f"Port {s.port} is a live-trading port; use 7497 (TWS paper) or 4002 (Gateway paper).")
    if s.port not in PAPER_PORTS:
        raise PaperOnlyError(f"Port {s.port} is not a known paper port (7497 or 4002).")


def check_paper_accounts(accounts):
    """IBKR paper account ids start with 'D' (e.g. DU1234567). Refuse anything else."""
    if not accounts:
        raise PaperOnlyError("TWS reported no managed accounts.")
    live = [a for a in accounts if not a.startswith("D")]
    if live:
        raise PaperOnlyError(f"Connected session includes non-paper account(s) {live}; refusing to trade.")
