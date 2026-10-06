"""One-time (and weekly) Schwab login.

    python -m schwab_mcp.auth            # log in and save tokens
    python -m schwab_mcp.auth --status   # show when the saved session expires

Opens Schwab's login page in your browser. After you approve, the browser is
sent to your callback URL (it will probably show a "can't connect" page; that
is expected). Copy the full address from the browser bar and paste it here.
"""

from __future__ import annotations

import argparse
import datetime as dt
import secrets
import sys
import time
import webbrowser
from pathlib import Path

if not __package__:
    # Run as ``python schwab_mcp/auth.py``: make the package importable.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from schwab_mcp.config import load_settings  # noqa: E402
from schwab_mcp.errors import SchwabError  # noqa: E402
from schwab_mcp.formatting import mask_account  # noqa: E402
from schwab_mcp.oauth import (  # noqa: E402
    build_authorize_url,
    code_from_redirect,
    exchange_code,
    refresh_token_expires_at,
)
from schwab_mcp.tokens import make_store  # noqa: E402


def _fmt_time(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def status() -> int:
    settings = load_settings()
    store = make_store(settings)
    token = store.load()
    if not token:
        print(f"No saved Schwab session in {store.describe()}. Run: python -m schwab_mcp.auth")
        return 1
    expires = refresh_token_expires_at(token)
    left = expires - time.time()
    if left <= 0:
        print(f"Session expired on {_fmt_time(expires)}. Run: python -m schwab_mcp.auth")
        return 1
    print(f"Session valid until about {_fmt_time(expires)} ({left / 86400:.1f} days left). Stored in {store.describe()}.")
    return 0


def login(verify: bool) -> int:
    settings = load_settings()
    store = make_store(settings)
    state = secrets.token_urlsafe(16)
    url = build_authorize_url(settings, state)

    print("Opening Schwab's login page in your browser...")
    if not webbrowser.open(url):
        # The URL contains your app key (a client id), so only show it when we must.
        print("Could not open a browser. Open this URL yourself:\n")
        print(url, "\n")
    print("Log in, approve access for your accounts, then copy the full URL your browser")
    print(f"lands on (it starts with {settings.callback_url}).")
    redirected = input("\nPaste the redirect URL here: ").strip()

    with httpx.Client(timeout=20.0) as http:
        code = code_from_redirect(redirected, state)
        token = exchange_code(settings, code, http)
        store.save(token)
        print(f"\nSaved tokens to {store.describe()}" + (" (permissions 0600)." if settings.token_store == "file" else "."))
        print(f"Re-run this script before {_fmt_time(refresh_token_expires_at(token))} (Schwab sessions last 7 days).")

        if verify:
            resp = http.get(
                "https://api.schwabapi.com/trader/v1/accounts/accountNumbers",
                headers={"Authorization": f"Bearer {token['access_token']}", "Accept": "application/json"},
            )
            if resp.status_code == 200:
                accounts = resp.json() or []
                masked = ", ".join(mask_account(a.get("accountNumber")) for a in accounts) or "none"
                print(f"Verified: {len(accounts)} linked account(s): {masked}")
            else:
                print(f"Tokens saved, but the test call returned HTTP {resp.status_code}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Log in to Schwab and save OAuth tokens for the MCP server.")
    parser.add_argument("--status", action="store_true", help="show when the saved session expires, then exit")
    parser.add_argument("--no-verify", action="store_true", help="skip the read-only test call after login")
    args = parser.parse_args(argv)
    try:
        return status() if args.status else login(verify=not args.no_verify)
    except SchwabError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
