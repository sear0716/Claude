"""Optional: have Claude turn the JSON report into a short written market briefing.

Requires the `anthropic` package and credentials (ANTHROPIC_API_KEY or `ant auth login`).
Claude only explains the signals already computed; it does not change them.
"""

from __future__ import annotations

import json

MODEL = "claude-opus-5-5"

SYSTEM = (
    "You are a markets analyst writing a pre-market briefing for an experienced trader. "
    "You are given a JSON report produced by a rule-based agent: FRED macro regime, GDELT news "
    "tone, SEC XBRL fundamentals, and technical signals (50/200-day SMA, RSI, MACD, ATR stops) "
    "for US large caps. Explain the top entry candidates, any exits on the trader's IBKR "
    "positions, and how macro and news context should temper conviction. Use only numbers "
    "present in the JSON; never invent prices, levels or events. Note data gaps listed under "
    "errors. Keep it under 400 words, in Markdown, and end with a one-line reminder that these "
    "are rule-based signals, not advice."
)


def _compact(report: dict) -> dict:
    """Drop the full universe table to keep the prompt small; the shortlist carries the signal."""
    slim = {k: v for k, v in report.items() if k != "universe"}
    slim["sector_leaders"] = {
        k: {f: v.get(f) for f in ("symbol", "action", "score", "trend", "rsi")}
        for k, v in report.get("sector_leaders", {}).items()
    }
    return slim


def write_briefing(report: dict, model: str = MODEL) -> str:
    import anthropic

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        output_config={"effort": "medium"},
        # If a safety classifier declines, let the API re-route instead of failing the run.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[
            {
                "role": "user",
                "content": "Signals report:\n```json\n" + json.dumps(_compact(report), default=str) + "\n```",
            }
        ],
    )
    if response.stop_reason == "refusal":
        return "_Briefing unavailable: the model declined this request._"
    return "".join(block.text for block in response.content if block.type == "text").strip()
