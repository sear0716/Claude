"""Shared HTTP client: retries, per-host throttling and a small on-disk cache."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class HttpClient:
    def __init__(
        self,
        user_agent: str,
        cache_dir: Path | None = None,
        min_interval: dict[str, float] | None = None,
        timeout: float = 30.0,
    ):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        retry = Retry(
            total=4,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.cache_dir = cache_dir
        self.min_interval = min_interval or {}
        self.timeout = timeout
        self._last_call: dict[str, float] = {}
        self._lock = threading.Lock()

    def _throttle(self, host: str) -> None:
        interval = self.min_interval.get(host, 0.0)
        if not interval:
            return
        with self._lock:
            wait = self._last_call.get(host, 0.0) + interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_call[host] = time.monotonic()

    def _cache_path(self, url: str, params: dict | None) -> Path | None:
        if self.cache_dir is None:
            return None
        key = url + "?" + json.dumps(params or {}, sort_keys=True)
        return self.cache_dir / "http" / (hashlib.sha256(key.encode()).hexdigest() + ".body")

    def get_text(
        self,
        url: str,
        params: dict | None = None,
        ttl: float = 0,
        headers: dict | None = None,
    ) -> str:
        path = self._cache_path(url, params) if ttl else None
        if path and path.exists() and time.time() - path.stat().st_mtime < ttl:
            return path.read_text()
        self._throttle(urlparse(url).netloc)
        resp = self.session.get(url, params=params, timeout=self.timeout, headers=headers)
        resp.raise_for_status()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(resp.text)
        return resp.text

    def get_json(self, url: str, params: dict | None = None, ttl: float = 0, headers: dict | None = None):
        return json.loads(self.get_text(url, params=params, ttl=ttl, headers=headers))
