"""HTTP layer: retries/backoff, a global rate limiter, and conditional GETs.

Uses `requests` (a single shared Session) rather than an async stack — a
sibling scraping project found aiohttp unreliable in sandboxed CI and fell
back to synchronous requests, which is more than sufficient for this
thread-pooled, I/O-bound workload.

`conditional_get` is the one function every caller goes through, so retry,
backoff, and rate-limiting logic lives in exactly one place.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Self

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from caselaw_mirror.config import HttpConfig


class RateLimiter:
    """A simple global token-bucket-of-one rate limiter shared across worker threads."""

    def __init__(self, requests_per_second: float):
        self._min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self._min_interval


@dataclass
class FetchResult:
    status: str  # "ok" | "not_modified" | "error"
    content: bytes | None = None
    etag: str | None = None
    last_modified: str | None = None
    status_code: int | None = None
    error: str | None = None


class HttpClient:
    def __init__(self, config: HttpConfig, requests_per_second: float = 5.0):
        self._config = config
        self._rate_limiter = RateLimiter(requests_per_second)
        self._session = requests.Session()
        self._session.headers["User-Agent"] = config.user_agent
        retry = Retry(
            total=config.max_retries,
            backoff_factor=config.backoff_factor,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "HEAD"),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def conditional_get(
        self, url: str, etag: str | None = None, last_modified: str | None = None
    ) -> FetchResult:
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        self._rate_limiter.acquire()
        try:
            response = self._session.get(url, headers=headers, timeout=self._config.timeout_seconds)
        except requests.RequestException as e:
            return FetchResult(status="error", error=str(e))

        if response.status_code == 304:
            return FetchResult(
                status="not_modified",
                etag=response.headers.get("ETag", etag),
                last_modified=response.headers.get("Last-Modified", last_modified),
                status_code=304,
            )

        if not response.ok:
            return FetchResult(
                status="error",
                status_code=response.status_code,
                error=f"HTTP {response.status_code} for {url}",
            )

        return FetchResult(
            status="ok",
            content=response.content,
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            status_code=response.status_code,
        )

    def get_json(self, url: str) -> Any:
        result = self.conditional_get(url)
        if result.status != "ok" or result.content is None:
            raise RuntimeError(result.error or f"Failed to fetch JSON from {url}")
        return json.loads(result.content)
