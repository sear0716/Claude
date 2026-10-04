"""Market-signal agent: IBKR + SEC EDGAR + FRED + GDELT -> SMA/RSI/MACD/ATR entries and exits."""

from .agent import Report, TradingAgent
from .config import Config

__all__ = ["Config", "Report", "TradingAgent"]
