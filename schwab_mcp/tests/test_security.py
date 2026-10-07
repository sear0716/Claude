"""Read-only enforcement and secret hygiene."""

import json
import logging
import re
from pathlib import Path

import pytest

from .conftest import FAKE_ACCESS, FAKE_REFRESH, FAKE_SECRET, FULL_ACCOUNT_NUMBERS, call, load

PACKAGE = Path(__file__).resolve().parent.parent
# Our own code only: skip the tests and a virtualenv created inside the package folder (as the README does).
SOURCES = [p for p in PACKAGE.rglob("*.py") if not {"tests", ".venv"} & set(p.relative_to(PACKAGE).parts)]
WRITE_PATTERNS = [
    r"\.(post|put|patch|delete)\(",
    r"method\s*=\s*[\"'](POST|PUT|PATCH|DELETE)",
    r"\.request\(|\.stream\(|\.send\(",
    r"/orders",
    r"previewOrder",
    r"place_order|replace_order|cancel_order",
]


def test_sources_found():
    assert {p.name for p in SOURCES} >= {"server.py", "client.py", "oauth.py", "auth.py"}


@pytest.mark.parametrize("pattern", WRITE_PATTERNS)
def test_no_write_calls_in_source(pattern):
    offenders = []
    for path in SOURCES:
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("#") or not re.search(pattern, line):
                continue
            # The only allowed POST: the OAuth token endpoint (code exchange and refresh).
            if path.name == "oauth.py" and re.search(r"\.post\(", line):
                continue
            # The client's guard that refuses order paths.
            if path.name == "client.py" and '"/orders" in path' in line:
                continue
            offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, offenders


def test_oauth_posts_only_to_token_endpoint():
    source = (PACKAGE / "oauth.py").read_text()
    assert source.count(".post(") == 2
    assert re.findall(r"_token_request\(", source)  # both posts go through the TOKEN_URL helper
    assert '"url": TOKEN_URL' in source


@pytest.fixture
def full_api(api, backend):
    api.get("/trader/v1/accounts/accountNumbers").respond(json=load("account_numbers.json"))
    api.get("/trader/v1/accounts").respond(json=load("accounts.json"))
    api.get("/trader/v1/accounts/HASH_AAA111").respond(json=load("account_positions.json"))
    api.get("/marketdata/v1/quotes").respond(json=load("quotes.json"))
    api.get("/marketdata/v1/pricehistory").respond(json=load("price_history.json"))
    api.get("/marketdata/v1/chains").respond(json=load("option_chain.json"))
    api.get("/marketdata/v1/markets").respond(json=load("market_hours.json"))
    return api


CALLS = [
    ("get_accounts", {}),
    ("get_account_summary", {}),
    ("get_account_summary", {"account": "5678", "detail": True}),
    ("get_positions", {"account": "5678"}),
    ("get_quote", {"symbols": ["AAPL"]}),
    ("get_price_history", {"symbol": "AAPL"}),
    ("get_option_chain", {"symbol": "AAPL", "from_date": "2025-01-01", "full": True}),
    ("get_market_hours", {}),
]


@pytest.mark.parametrize("name,args", CALLS)
def test_no_secrets_or_full_account_numbers_in_output_or_logs(full_api, caplog, name, args):
    caplog.set_level(logging.DEBUG)
    result = call(name, **args)
    assert not result.is_error, result.content
    blob = json.dumps(result.structured_content) + " ".join(getattr(c, "text", "") for c in result.content)
    blob += caplog.text
    for secret in (FAKE_ACCESS, FAKE_REFRESH, FAKE_SECRET, *FULL_ACCOUNT_NUMBERS):
        assert secret not in blob, f"{secret} leaked from {name}"
