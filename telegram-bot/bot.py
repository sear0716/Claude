#!/usr/bin/env python3
"""Telegram bot that relays your messages to Claude and sends back the reply.

    export TELEGRAM_BOT_TOKEN="BOT KEY"        # from @BotFather
    export ANTHROPIC_API_KEY="ANT KEY"         # from console.anthropic.com
    export ALLOWED_USER_IDS=123456789    # comma-separated Telegram user ids
    python bot.py

Each chat keeps its own conversation history in memory; /reset clears it.
Only users listed in ALLOWED_USER_IDS get answers. Anyone else (including you,
before you've set it) is told their user id so you can add it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections import defaultdict

import anthropic
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

MODEL = os.environ.get("TELEGRAM_BOT_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("TELEGRAM_BOT_EFFORT", "medium")  # low | medium | high | xhigh | max
SYSTEM_PROMPT = os.environ.get(
    "TELEGRAM_BOT_SYSTEM_PROMPT",
    "You are Claude, chatting with the user through Telegram. Keep replies concise and "
    "readable on a phone. Telegram shows your reply as plain text, so avoid Markdown "
    "headings and tables.",
)
TELEGRAM_MAX_CHARS = 4096

log = logging.getLogger("telegram-bot")


def build_client() -> anthropic.AsyncAnthropic:
    """Create the Claude client.

    API keys that aren't tied to a workspace need the workspace id sent as a header
    (otherwise the API answers 400 "not scoped to a workspace"). Set
    ANTHROPIC_WORKSPACE_ID to enable that; leave it unset for workspace-scoped keys.
    """
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    if workspace_id:
        return anthropic.AsyncAnthropic(default_headers={"anthropic-workspace-id": workspace_id})
    return anthropic.AsyncAnthropic()


def parse_allowed_ids(raw: str) -> set[int]:
    return {int(part) for part in raw.replace(" ", "").split(",") if part}


def split_message(text: str, limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    """Split text into Telegram-sized chunks, preferring paragraph/line breaks."""
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut <= 0:
            cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


class ClaudeChat:
    """Per-chat conversation history and the call to Claude."""

    def __init__(self, client: anthropic.AsyncAnthropic):
        self.client = client
        self.histories: dict[int, list] = defaultdict(list)
        self.locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def reset(self, chat_id: int) -> None:
        self.histories.pop(chat_id, None)

    async def reply(self, chat_id: int, user_text: str) -> str:
        async with self.locks[chat_id]:
            messages = self.histories[chat_id]
            messages.append({"role": "user", "content": user_text})
            try:
                response = await self.client.beta.messages.create(
                    model=MODEL,
                    max_tokens=16000,
                    system=SYSTEM_PROMPT,
                    output_config={"effort": EFFORT},
                    # If the model declines on safety grounds, the API retries the
                    # request on a suitable fallback model within the same call.
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                    messages=messages,
                )
            except Exception:
                messages.pop()  # don't leave a dangling user turn behind
                raise

            if response.stop_reason == "refusal":
                messages.pop()
                return "Claude declined to answer that one. Try rephrasing, or /reset to start over."

            # Keep the full content (thinking/fallback blocks included) so the next
            # request replays this turn exactly as the API returned it.
            messages.append({"role": "assistant", "content": response.content})
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            if response.stop_reason == "max_tokens":
                text += "\n\n[reply cut off at the length limit]"
            return text or "(empty reply)"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Hi! Send me a message and I'll pass it to Claude. /reset starts a new conversation."
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.bot_data["chat"].reset(update.effective_chat.id)
    await update.effective_message.reply_text("Conversation cleared.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat: ClaudeChat = context.bot_data["chat"]
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)

    try:
        text = await chat.reply(chat_id, update.effective_message.text)
    except anthropic.AuthenticationError:
        log.exception("Anthropic authentication failed")
        text = "Claude API key was rejected. Check ANTHROPIC_API_KEY on the server."
    except anthropic.RateLimitError:
        text = "Claude is rate limited right now; try again in a minute."
    except anthropic.APIStatusError as e:
        log.exception("Claude API error (request id %s)", getattr(e, "request_id", None))
        text = f"Claude API error ({e.status_code}). Try again shortly."
    except anthropic.APIConnectionError:
        log.exception("Could not reach the Claude API")
        text = "Couldn't reach Claude. Check the server's network connection."

    for chunk in split_message(text):
        await update.effective_message.reply_text(chunk)


async def unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    log.warning("Ignored message from unlisted user %s (%s)", user.id, user.username)
    await update.effective_message.reply_text(
        f"This bot is private. Your Telegram user id is {user.id}; "
        "the owner can add it to ALLOWED_USER_IDS."
    )


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines include the bot token

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("Set TELEGRAM_BOT_TOKEN to the token BotFather gave you.")
    allowed = parse_allowed_ids(os.environ.get("ALLOWED_USER_IDS", ""))
    if not allowed:
        log.warning("ALLOWED_USER_IDS is empty: the bot will only reply with each sender's user id.")

    app = Application.builder().token(token).build()
    app.bot_data["chat"] = ClaudeChat(build_client())

    allowed_filter = filters.User(user_id=allowed) if allowed else filters.User(user_id=[])
    app.add_handler(CommandHandler("start", start, filters=allowed_filter))
    app.add_handler(CommandHandler("reset", reset, filters=allowed_filter))
    app.add_handler(MessageHandler(allowed_filter & filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(~allowed_filter & filters.TEXT, unauthorized))

    log.info("Bot running with model %s. Press Ctrl+C to stop.", MODEL)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
