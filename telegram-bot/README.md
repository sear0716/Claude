# Telegram → Claude bot

Chat with Claude from Telegram. `bot.py` long-polls Telegram (no public URL or webhook needed), sends each message to the Claude API with the chat's history, and replies with Claude's answer.

## Setup

1. **Get a Claude API key** at <https://console.anthropic.com> → API Keys. (A Claude.ai Pro/Max subscription does not include API access; the API is billed separately.)
2. **Install** (Python 3.10+):

   ```bash
   cd telegram-bot
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Set your secrets as environment variables.** Never commit them.

   ```bash
   export TELEGRAM_BOT_TOKEN="123456:ABC..."   # from @BotFather
   export ANTHROPIC_API_KEY="sk-ant-..."
   ```

4. **Find your Telegram user id:** run `python bot.py`, message your bot, and it replies with your id. Stop it (Ctrl+C), then:

   ```bash
   export ALLOWED_USER_IDS="123456789"         # comma-separate several ids
   python bot.py
   ```

   Only listed users get Claude replies, so strangers who find your bot can't spend your API credits.

The bot only runs while `python bot.py` is running. To keep it up 24/7, run it on an always-on machine or small VPS (e.g. under `systemd`, `tmux`, or Docker).

## Using it

- Send any text message to talk to Claude. Each chat has its own conversation memory.
- `/reset` starts a fresh conversation (history is also cleared whenever the bot restarts).

## Options

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_MODEL` | `claude-opus-5-5` | Claude model id, e.g. `claude-sonnet-5-5` for cheaper replies |
| `TELEGRAM_BOT_EFFORT` | `medium` | `low` is faster/cheaper for casual chat; `high` for harder questions |
| `TELEGRAM_BOT_SYSTEM_PROMPT` | short Telegram-friendly prompt | Custom persona/instructions |

Requests opt into server-side refusal fallbacks (`fallbacks: "default"`), so if the model declines a request on safety grounds the API retries it on a suitable fallback model in the same call.

## Tests

```bash
pytest -q
```
