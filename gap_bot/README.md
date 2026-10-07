# gap_bot: gap-and-go paper trading bot for Interactive Brokers

A Python build of the bot described in [How to Build an AI Trading Bot with Claude Code and Interactive Brokers](https://yourleadingwebinar.com/2026/05/21/how-to-build-an-ai-trading-bot-with-claude-code-and-interactive-brokers/). It scans the S&P 500 for stocks gapping up at the open, buys breakouts that pass six rules, manages the trade with a stop, a partial exit, a breakeven move and a trailing stop, and closes everything before the bell. It uses `ib_async` against TWS or IB Gateway.

**Paper trading only.** The bot refuses to start unless `PAPER_TRADING=true` and the port is a paper port (7497 TWS, 4002 Gateway), and after connecting it refuses to trade if any account on the session doesn't start with `D` (paper accounts are `DU…`). There is no switch to turn that off.

> This is an educational project, not investment advice. Paper trade it for weeks and read the backtest caveats below before trusting any of it.

## Strategy (all values live in `rules.json`)

| Rule | Meaning |
|---|---|
| Universe | S&P 500 (`sp500.txt`), price ≥ $3, opening gap ≥ 3%, top 20 by gap |
| D1 | Last price above the previous day's high |
| D2 | Previous close above the 200-day SMA |
| D3 | Opening gap ≥ 3% vs the previous close |
| I1 | Last price above the premarket high |
| I2 | Last 5-min bar closes above every earlier bar's high today (new high of day) |
| I3 | Relative volume ≥ 2.0: today's volume so far vs the 14-day average daily volume, pro-rated to the time elapsed |
| Entry window | 10:05–15:30 ET; one entry per symbol per day; max 5 open positions, max 5 entries per day |
| Size | Risk 1% of `PORTFOLIO_VALUE_USD` to the stop, capped at 10% of the portfolio and `MAX_TRADE_SIZE_USD` |
| Initial stop | Low of day − 1% (a real stop order at IBKR) |
| Partial | At +0.75R sell 1/3 at market |
| Breakeven | At +1.0R move the stop to the entry price |
| Trail | After breakeven, raise the stop to the latest 5-min swing low (lower than 2 bars each side) − $0.01 |
| Force close | 15:51 ET, everything sold at market |

R is the distance from entry to the initial stop.

## Layout

| File | What it does |
|---|---|
| `strategy.py` | Pure rule logic (time gates, D1–D3/I1–I3, sizing, exits). Shared by the live loop and the backtest so they can't drift apart. |
| `cycle.py` | One live pass: detect stop-outs (matched by stop order id), manage open positions, force-close, then look for entries. |
| `broker.py` | `ib_async` wrapper: bars, positions, market orders, stop orders, fills. |
| `prefilter.py` | Morning gap scan of the S&P 500 via yfinance → `state/watchlist.txt`. |
| `backtest.py` | Bar-by-bar replay of the same rules on yfinance, IBKR or CSV data. |
| `perf.py` | Round-trip P&L, win rate, profit factor, R histogram, drawdown, HTML dashboard. |
| `state.py` | `state/open_positions.json` (atomic writes), `state/trades.csv`, `logs/safety_log.jsonl`. |
| `notify.py` | Optional Telegram alerts with ntfy fallback; never raises. |

## Setup

1. In TWS (logged into your **paper** account): File > Global Configuration > API > Settings, tick **Enable ActiveX and Socket Clients**, untick **Read-Only API**, socket port **7497**, trusted IP 127.0.0.1.
2. From the repo root:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate            # Windows: .venv\Scripts\activate
   pip install -r gap_bot/requirements.txt
   cp gap_bot/.env.example gap_bot/.env  # then edit portfolio size, alerts
   ```

## Run it

```bash
python -m gap_bot prefilter              # today's gap list -> gap_bot/state/watchlist.txt
python -m gap_bot check NVDA             # show every rule for one symbol, never trades
python -m gap_bot cycle --dry-run        # one pass, logs what it would do
python -m gap_bot run --dry-run          # whole session, no orders
python -m gap_bot run                    # whole session, paper orders
python -m gap_bot report                 # stats + gap_bot/dashboard/index.html
```

`run` stays up for the day: it refreshes the watchlist every 30 minutes from 09:55 to 13:00 ET, runs a cycle 10 seconds after every 5-minute bar closes, and exits after 16:00. Start it each weekday around 09:25 ET with Windows Task Scheduler (action: `.venv\Scripts\python.exe -m gap_bot run`, start in: the repo folder) or cron on macOS/Linux (`25 9 * * 1-5 cd /path/to/Claude && .venv/bin/python -m gap_bot run`, with cron's timezone set to America/New_York). Keep the machine awake and TWS logged in. Market holidays aren't in the calendar; on those days the scan finds nothing and the bot idles.

Every decision, including each rule's pass/fail and the numbers behind it, is appended to `gap_bot/logs/safety_log.jsonl`.

## Backtest

```bash
python -m gap_bot backtest AAPL NVDA TSLA          # yfinance: ~60 days of 5-min bars (Yahoo's limit)
python -m gap_bot backtest                          # whole S&P 500 (slow: two downloads per symbol)
python -m gap_bot backtest --source ibkr AMD MU     # 5-min history from TWS (needs market data permissions)
python -m gap_bot backtest --source csv --csv-dir data   # <SYM>_daily.csv + <SYM>_5min.csv
python -m gap_bot backtest --rules my_rules.json --slippage 0.02
```

Results go to `gap_bot/backtests/<timestamp>/` (`trades.csv`, `equity.csv`, `summary.json`) and the summary prints to the terminal.

How fills are modelled, so the numbers aren't flattered by hindsight: decisions use only completed bars; entries, partials and force-closes fill at the next bar's open; stops fill at the stop or at the bar's open if it gapped through; the gap scan uses the 09:30 open; commission is $0.005/share with a $1 minimum. Still not modelled: queue position, halts, partial fills, borrow/short constraints (the bot is long-only), and survivorship bias (`sp500.txt` is today's index, not the index on each historical day). Yahoo only serves about 60 days of 5-minute data, so treat any backtest as a sanity check, not proof of an edge.

## Tests

```bash
python -m pytest gap_bot/tests
```

The tests run offline with synthetic bars and a fake broker, covering the rules, sizing, exits, the paper-only guard, the live cycle (entry, partial, breakeven, stop-out, force close, dry run, daily caps) and the backtest fill model.

## Differences from the article

- One long-running `run` process instead of 11 Windows Task Scheduler jobs, so it works the same on Windows, macOS and Linux and keeps one IBKR connection (and its order ids) for the day.
- Orders are placed in-process with one client id instead of a `trade.py` subprocess with a second client id; modifying the stop order needs the same client id that placed it.
- Added: one entry per symbol per day (otherwise a stopped-out name can be re-bought on the next cycle), a hard paper-account check, and a backtest that reuses the live rule code. The article's backtest lived in TradingView/Pine Script.
- Trailing starts after the breakeven move, using swing lows formed since entry.
