"""Exceptions surfaced to tool callers. Messages must never contain secrets."""

from __future__ import annotations

SESSION_EXPIRED_MESSAGE = "Schwab session expired, run auth.py to re-authenticate."


class SchwabError(Exception):
    """Base class: ``str(err)`` is safe to show to the user."""


class ConfigError(SchwabError):
    pass


class SessionExpired(SchwabError):
    def __init__(self, detail: str | None = None):
        message = SESSION_EXPIRED_MESSAGE
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)


class InvalidInput(SchwabError):
    pass


class SchwabAPIError(SchwabError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status
