# Claude
Claude Projects

- [`telegram-bot/`](telegram-bot/README.md): chat with Claude from Telegram using a BotFather token.

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
