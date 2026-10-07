# Schwab MCP server (read-only)

A local MCP server (stdio) that lets Claude Code and Claude Desktop read your Charles Schwab account and market data. It cannot place, change or cancel orders. The code contains no order or other write endpoints, and a test fails if one is ever added.

| Tool | What it returns |
|---|---|
| `get_accounts` | Linked accounts: masked number (`****1234`), account type, and the `account_hash` the API needs |
| `get_account_summary` | Liquidation value, equity, cash, buying power, margin figures. `account` (last 4 or hash) is optional, and `detail=true` adds Schwab's full balance blocks |
| `get_positions` | Each holding's quantity, average price, cost basis, market value, day P/L, total P/L and `pct_account` (percent of the account's total value, cash included), plus totals |
| `get_quote` | Quotes for up to 50 symbols (`AAPL`, `BRK.B`, `$SPX`, `/ES`). `include_fundamentals=true` adds P/E, EPS and dividend data |
| `get_price_history` | OHLCV candles as compact rows. Set `period_type`/`period`/`frequency_type`/`frequency`, or use `start_date`/`end_date` |
| `get_option_chain` | By default: 10 strikes around the money and the next 3 expirations within 60 days, in compact rows. Widen it with `strike_count`, `max_expirations`, `from_date`, `to_date`, `strike_range`, `contract_type`, or `full=true` |
| `get_market_hours` | Session times for equity/option/bond/future/forex and whether the regular session is open right now |

Account numbers are masked to the last 4 digits in every response and error. Tokens and your app secret are never logged or returned.

## 1. Create the Schwab app

1. Sign in at [developer.schwab.com](https://developer.schwab.com) with your Schwab login, then go to **Dashboard > Apps > Create App**.
2. Add the API products **Accounts and Trading Production** (needed to read balances and positions) and **Market Data Production**. Schwab doesn't offer a read-only version of account access. This server enforces read-only in its own code.
3. Set the **Callback URL** to `https://127.0.0.1:8182`. It must match `SCHWAB_CALLBACK_URL` exactly, and nothing needs to be listening on that port.
4. Wait for the app status to change from *Approved - Pending* to **Ready For Use**. This can take a few days, and changing the callback URL later usually needs re-approval. Then copy the **App Key** and **Secret** from the app page.

## 2. Install

Requires Python 3.11 or newer. From the repo root:

```bash
python3 -m venv schwab_mcp/.venv
source schwab_mcp/.venv/bin/activate          # Windows: schwab_mcp\.venv\Scripts\activate
pip install -r schwab_mcp/requirements.txt
# or with uv:  uv venv schwab_mcp/.venv && uv pip install -r schwab_mcp/requirements.txt --python schwab_mcp/.venv
```

Create your settings file and fill it in yourself (it is git-ignored):

```bash
cp schwab_mcp/.env.example schwab_mcp/.env
chmod 600 schwab_mcp/.env
```

| Variable | Meaning |
|---|---|
| `SCHWAB_APP_KEY`, `SCHWAB_APP_SECRET` | From your app page |
| `SCHWAB_CALLBACK_URL` | Exactly the app's callback URL |
| `TOKEN_PATH` | Where tokens are saved, default `~/.schwab_mcp/schwab_token.json`. The file is written with `0600` permissions in a `0700` folder |
| `TOKEN_STORE` | `file` (default) or `keyring` to keep tokens in the macOS Keychain, Windows Credential Manager or Secret Service instead (`pip install keyring`) |

To keep the `.env` somewhere else, point `SCHWAB_ENV_FILE` at it.

## 3. First-time login

```bash
python -m schwab_mcp.auth
```

Your browser opens Schwab's login page. Sign in, tick the accounts to share, and approve. The browser then goes to `https://127.0.0.1:8182/?code=...` and shows a "can't connect" page, which is expected. Copy the **whole address** from the address bar and paste it into the terminal within about 30 seconds, because the code expires quickly. The script saves the tokens, then makes one read-only call and prints your masked account numbers to confirm everything works.

## 4. Register the server

Use absolute paths. Both clients start the server themselves.

**Claude Code**

```bash
claude mcp add schwab --scope user -- /ABS/PATH/Claude/schwab_mcp/.venv/bin/python /ABS/PATH/Claude/schwab_mcp
```

Run `claude mcp list` to check that it shows as connected.

**Claude Desktop**: edit `claude_desktop_config.json`. On macOS it is in `~/Library/Application Support/Claude/` and on Windows in `%APPDATA%\Claude\`.

```json
{
  "mcpServers": {
    "schwab": {
      "command": "/ABS/PATH/Claude/schwab_mcp/.venv/bin/python",
      "args": ["/ABS/PATH/Claude/schwab_mcp"]
    }
  }
}
```

On Windows, use `C:\\path\\to\\Claude\\schwab_mcp\\.venv\\Scripts\\python.exe` for the command. Restart Claude Desktop after editing.

## 5. Weekly re-login

Access tokens last 30 minutes, and the server refreshes them automatically. The **refresh token expires 7 days after you log in** and refreshing does not extend it. After that, every tool returns *"Schwab session expired, run auth.py to re-authenticate"*. Run `python -m schwab_mcp.auth` again; you don't need to restart the server.

To see how long the current session has left:

```bash
python -m schwab_mcp.auth --status
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `pip install` fails building `cryptography` (OpenSSL / pkg-config / Rust errors) on an Intel Mac | Pull the latest `main`, which pins `cryptography<49` there (newer versions have no Intel-Mac wheels), then run `pip install -r schwab_mcp/requirements.txt` again |
| `Missing SCHWAB_APP_KEY ...` | `schwab_mcp/.env` is missing or incomplete, or `SCHWAB_ENV_FILE` points to the wrong place |
| `Schwab session expired, run auth.py ...` | The 7 days are up, the token file is missing, or Schwab revoked it. Run `python -m schwab_mcp.auth` |
| Token exchange failed | You took more than about 30 seconds, pasted part of the URL, or the callback URL differs from the app's. Run auth again |
| Login page says the app is invalid, or `redirect_uri` doesn't match | The app isn't *Ready For Use* yet, or `SCHWAB_CALLBACK_URL` doesn't match the portal exactly (watch for trailing slashes) |
| `HTTP 403` | The app is missing a product, or that account wasn't ticked during login. Re-run auth and select it |
| `rate limit (HTTP 429)` | The server already retried with backoff. Wait a minute before trying again |
| `Schwab is having trouble (HTTP 5xx)` | Schwab outage or maintenance. Try later |
| Tools don't show up in Claude | Use absolute paths, check that the venv's python has the requirements installed, and run `claude mcp list` or look at Claude Desktop's MCP logs |
| Delayed quotes | `realtime: false` in the quote means Schwab sent delayed data. Check your market data agreements on schwab.com |

The server logs warnings to stderr only (stdout carries the MCP protocol), and it never logs tokens, request headers or account numbers.

## Tests

```bash
pip install -r schwab_mcp/requirements-dev.txt
python -m pytest schwab_mcp/tests
```

All HTTP calls are mocked with `respx`. The tests need no credentials and never contact Schwab. They cover every tool, input validation, 429/401/403/5xx handling, token refresh and expiry, 0600 token files, account masking, and a scan that fails if any write call or order endpoint appears in the source.

## Notes

- The endpoints and field names follow Schwab's Trader API as implemented by schwab-py 1.5.1. They still need to be checked against Schwab's official spec pages, so the first live run is also a check on field names.
- This server uses its own small httpx client rather than schwab-py, so the codebase has no order-placement code at all.
