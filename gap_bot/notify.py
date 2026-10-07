"""Fire-and-forget alerts: Telegram first, ntfy as fallback. Never raises."""
import logging

import requests

log = logging.getLogger(__name__)


def notify(settings, title, body=""):
    text = f"*{title}*\n{body}".strip()
    if settings.telegram_token and settings.telegram_chat_id:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage",
                json={"chat_id": settings.telegram_chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=5,
            )
            if r.ok:
                return True
        except Exception as exc:  # noqa: BLE001 - alerts must never break trading
            log.warning("telegram alert failed: %s", exc)
    if settings.ntfy_topic:
        try:
            requests.post(f"https://ntfy.sh/{settings.ntfy_topic}", data=body.encode(),
                          headers={"Title": title, "Priority": "default"}, timeout=5)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("ntfy alert failed: %s", exc)
    log.info("ALERT %s %s", title, body)
    return False
