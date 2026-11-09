"""Market-data sources for the scanner.

Each source provides:
    history(symbol, days)      -> DataFrame[open, high, low, close, volume] (daily, oldest first)
    spot(symbol)               -> float | None
    chain(symbol, min_dte, max_dte, lo_k, hi_k) -> DataFrame (see CHAIN_COLUMNS)
    iv_history(symbol)         -> Series of the stock's ~30-day implied vol, or None
    dividend_yield(symbol)     -> float | None (continuous annual decimal)
    close()
"""

from .base import CHAIN_COLUMNS, empty_chain
from .demo import DemoSource
from .ibkr import IBKROptionsSource
from .yahoo import YahooSource

__all__ = ["CHAIN_COLUMNS", "empty_chain", "DemoSource", "IBKROptionsSource", "YahooSource"]
