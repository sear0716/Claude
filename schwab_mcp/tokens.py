"""Token persistence: a 0600 JSON file (default) or the OS keychain."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Protocol

from .config import Settings
from .errors import ConfigError

KEYRING_SERVICE = "schwab-mcp"
KEYRING_USER = "oauth-token"


class TokenStore(Protocol):
    def load(self) -> dict[str, Any] | None: ...
    def save(self, token: dict[str, Any]) -> None: ...
    def describe(self) -> str: ...


class FileTokenStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def describe(self) -> str:
        return str(self.path)

    def load(self) -> dict[str, Any] | None:
        try:
            mode = stat.S_IMODE(self.path.stat().st_mode)
        except FileNotFoundError:
            return None
        if mode & 0o077 and os.name == "posix":
            # Tighten permissions if someone loosened them.
            os.chmod(self.path, 0o600)
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save(self, token: dict[str, Any]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Write to a 0600 temp file in the same directory, then atomically replace.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".schwab_token.", suffix=".tmp")
        try:
            if os.name == "posix":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(token, f)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
        if os.name == "posix":
            os.chmod(self.path, 0o600)


class KeyringTokenStore:
    def __init__(self):
        try:
            import keyring  # noqa: F401
        except ImportError as exc:
            raise ConfigError("TOKEN_STORE=keyring needs the 'keyring' package: pip install keyring") from exc
        import keyring

        self._keyring = keyring

    def describe(self) -> str:
        return f"OS keychain ({KEYRING_SERVICE})"

    def load(self) -> dict[str, Any] | None:
        raw = self._keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def save(self, token: dict[str, Any]) -> None:
        self._keyring.set_password(KEYRING_SERVICE, KEYRING_USER, json.dumps(token))


def make_store(settings: Settings) -> TokenStore:
    if settings.token_store == "keyring":
        return KeyringTokenStore()
    return FileTokenStore(settings.token_path)
