# Claude
Claude Projects

- [`telegram-bot/`](telegram-bot/README.md): chat with Claude from Telegram using a BotFather token.
- [`gap_bot/`](gap_bot/README.md): gap-and-go paper trading bot for Interactive Brokers (S&P 500 gap scanner, 5-minute trading loop, backtest).
- [`schwab_mcp/`](schwab_mcp/README.md): MCP server for your Schwab accounts and market data (read-only by default, opt-in guarded order tools) (Claude Code and Claude Desktop).

## IB account check

`ib_account_check.py` connects to a running TWS read-only (it cannot place orders) and prints the managed account(s), key account summary values and open positions.

1. In TWS, open **File > Global Configuration > API > Settings**, tick **Enable ActiveX and Socket Clients**, and confirm the socket port is **7497** (paper trading). Leave 127.0.0.1 in Trusted IPs.
2. From the repo root, create a virtual environment and install the pinned dependency:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. With TWS logged in to your paper account, run:

   ```bash
   python ib_account_check.py                 # defaults: 127.0.0.1, port 7497, client id 17
   python ib_account_check.py --port 4002     # IB Gateway (paper) instead of TWS
   ```

Paper account ids start with `D`; the script labels anything else `LIVE?` so you notice if you're pointed at a live session (7496 is live TWS). If you get a connection refused or timeout, check that TWS is running, the API is enabled, and no other client is already using the same `--client-id`.

## IB historical prices

`ib_history.py` pulls historical bars from the same running TWS, read-only, and writes one CSV per symbol into `data/` (git-ignored) so the files can be handed to Claude for analysis. Setup is the same as the account check above.

```bash
python ib_history.py AAPL                                    # 1 year of daily bars -> data/AAPL_1day_1Y.csv
python ib_history.py AAPL MSFT BRK.B --duration "5 Y"        # several symbols at once
python ib_history.py SPY --duration "30 D" --bar-size "5 mins"
python ib_history.py AAPL --what ADJUSTED_LAST               # split/dividend adjusted closes
```

Columns: `date,open,high,low,close,volume,average,bar_count`. Intraday bar times are UTC. It uses client id 18 by default so it can run alongside the account check. IB limits how much intraday history one request can return (for example about 1 month of 1-minute bars), and it needs market data permissions for the symbol; with none, the script prints "no bars returned" and the reason appears in TWS. Run the offline tests with `python -m pytest tests`.
