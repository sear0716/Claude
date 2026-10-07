"""Settings loaded from a local .env file (never from tool arguments)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .errors import ConfigError

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_TOKEN_PATH = "~/.schwab_mcp/schwab_token.json"
REQUIRED = ("SCHWAB_APP_KEY", "SCHWAB_APP_SECRET", "SCHWAB_CALLBACK_URL")


@dataclass(frozen=True)
class Settings:
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    callback_url: str
    token_path: Path
    token_store: str = "file"
    trading_enabled: bool = False
    max_order_value: float = 5000.0


def env_file_path() -> Path:
    """``SCHWAB_ENV_FILE`` if set, else ``schwab_mcp/.env`` next to this package."""
    override = os.environ.get("SCHWAB_ENV_FILE")
    return Path(override).expanduser() if override else PACKAGE_DIR / ".env"


def load_settings() -> Settings:
    # Values already in the process environment win over the .env file.
    load_dotenv(env_file_path(), override=False)
    missing = [name for name in REQUIRED if not os.environ.get(name, "").strip()]
    if missing:
        raise ConfigError(
            f"Missing {', '.join(missing)}. Copy schwab_mcp/.env.example to "
            f"{env_file_path()} and fill it in."
        )
    store = os.environ.get("TOKEN_STORE", "file").strip().lower() or "file"
    if store not in ("file", "keyring"):
        raise ConfigError("TOKEN_STORE must be 'file' or 'keyring'.")
    callback = os.environ["SCHWAB_CALLBACK_URL"].strip()
    if not callback.startswith("https://"):
        raise ConfigError("SCHWAB_CALLBACK_URL must start with https:// (Schwab requires HTTPS).")
    try:
        max_value = float(os.environ.get("SCHWAB_MAX_ORDER_VALUE", "5000") or "5000")
    except ValueError:
        raise ConfigError("SCHWAB_MAX_ORDER_VALUE must be a number.") from None
    if max_value <= 0:
        raise ConfigError("SCHWAB_MAX_ORDER_VALUE must be positive.")
    return Settings(
        app_key=os.environ["SCHWAB_APP_KEY"].strip(),
        app_secret=os.environ["SCHWAB_APP_SECRET"].strip(),
        callback_url=callback,
        token_path=Path(os.environ.get("TOKEN_PATH") or DEFAULT_TOKEN_PATH).expanduser(),
        token_store=store,
        trading_enabled=os.environ.get("SCHWAB_ENABLE_TRADING", "").strip().lower() == "true",
        max_order_value=max_value,
    )
