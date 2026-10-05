# Trading agent

Scans large-cap US stocks (NYSE + Nasdaq) across all 11 GICS sectors and suggests
**entries and exits** from technical indicators, adjusted by fundamentals, news and macro context.

| Source | What it supplies | Module |
|---|---|---|
| **Interactive Brokers** (TWS / IB Gateway) | Daily adjusted OHLCV, account net liquidation, open positions | `sources/ibkr.py` |
| **SEC EDGAR** | XBRL fundamentals (revenue growth, net income, EPS, shares outstanding → market cap), recent 8-K/10-Q/10-K filings | `sources/sec_edgar.py` |
| **FRED** | Fed funds, 2y/10y yields, 10y–2y curve, CPI, unemployment, payrolls, HY spreads, VIX → risk-on/neutral/risk-off regime | `sources/fred.py` |
| **GDELT** | 7-day news tone + headlines per company, plus a global geopolitical-risk query | `sources/gdelt.py` |

The IBKR connection is opened **read-only**. The agent never places orders.

## How it decides

1. **Universe.** Pulls 12–21 candidates per sector (`universe.py`), ranks them by live market cap
   (SEC shares outstanding × IBKR price), and keeps the top 10 per sector.
2. **Indicators** (`indicators.py`): 50/200-day SMA, RSI(14) (Wilder), MACD(12,26,9), ATR(14) (Wilder),
   and a 22-day chandelier stop.
3. **Signals** (`signals.py`):

| Signal | Rule |
|---|---|
| Uptrend | close > SMA200 and SMA50 > SMA200 |
| **BUY** | uptrend, MACD histogram > 0 (or a fresh bullish cross), RSI < 70, score ≥ threshold |
| **BUY_PULLBACK** | uptrend, RSI ≤ 40, close still above SMA200 |
| **SELL** (held positions) | close < SMA200, a death cross, close < chandelier stop, or a bearish MACD cross with RSI > 70 |
| Initial stop | entry − 2 × ATR |
| Trailing stop | 22-day high − 3 × ATR (never set below the initial stop on your cost basis) |
| Target | entry + 2R |
| Size | 1% of net liquidation at risk per trade, capped at 10% of equity per position |

   The score combines trend (±30), MACD (±15, ±10 for crosses), golden/death cross (±10), RSI zone,
   and extension above the 50-day. Adjustments: SEC revenue growth / losses (±5), GDELT tone (+3 / −5).
4. **Macro.** In a FRED **risk-off** regime the buy threshold rises by 15 points and position sizes are halved.
   In **risk-on** the threshold drops by 5.
5. **Output.** The top 10 BUY candidates (at most 3 per sector), exit/hold calls on your IBKR positions,
   and the best setup in each sector.

## Setup

```bash
pip install -r trading_agent/requirements.txt
```

1. In TWS or IB Gateway, enable **Configure → API → Settings → Enable ActiveX and Socket Clients**.
   Read-only API is fine. You need US equity market-data subscriptions for historical bars.
2. Set your environment:

```bash
export IB_PORT=7497              # 7497 paper TWS, 7496 live TWS, 4002/4001 Gateway
export IB_CLIENT_ID=17
export IB_CONNECT_ATTEMPTS=3      # optional; startup connect retries, waiting 2s, 4s, ... between them
export IB_CONNECT_BACKOFF=2      # optional; first retry delay in seconds
export SEC_USER_AGENT="Your Name your.email@example.com"   # SEC requires contact info
export FRED_API_KEY=...          # optional; free at fred.stlouisfed.org. Without it the public CSV endpoint is used
export ANTHROPIC_API_KEY=...     # optional, only for --briefing
```

## Usage

```bash
# Full run: IBKR prices + positions, SEC, FRED, GDELT
python -m trading_agent

# Faster: skip GDELT (it allows ~1 request / 5 s)
python -m trading_agent --no-news

# A specific watchlist
python -m trading_agent --symbols AAPL,MSFT,NVDA,JPM,XOM

# Without IBKR: CSV files named <SYMBOL>.csv with date,open,high,low,close[,volume]
python -m trading_agent --prices-dir ./prices --equity 50000

# Try the pipeline offline with synthetic prices (not market data)
python -m trading_agent --demo

# Add a Claude-written briefing on top of the computed signals
python -m trading_agent --briefing
```

Each run writes `reports/report.md`, `reports/report.json` and `reports/signals.csv`.
HTTP responses are cached under `~/.cache/trading_agent`. Set `TRADING_AGENT_CACHE` to move the cache.

To change the strategy, edit the parameters on `Config` in `config.py`: periods, ATR multipliers, risk per
trade, and the buy threshold. To change the stocks scanned, edit the sector lists in `universe.py` or pass
`--universe-file`.

## Tests

```bash
python -m pytest trading_agent/tests -q
```

The tests run fully offline. HTTP sources use recorded-shape fixtures and IBKR uses a fake client.

> Rule-based signals for research only. This is not investment advice. Check prices and levels before
> you place any order.
