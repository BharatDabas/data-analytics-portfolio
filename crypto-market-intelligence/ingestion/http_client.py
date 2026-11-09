"""HTTP access with client-side rate limiting, retry and exponential backoff.

`call_with_retry` is transport-agnostic so the same policy is applied to the
real CoinGecko adapter and to the synthetic provider's simulated faults.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable, TypeVar

import requests

T = TypeVar("T")


class TransientError(Exception):
    """Retryable failure (HTTP 429/5xx, timeout, connection reset)."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class IngestionError(Exception):
    """Non-retryable failure, or retries exhausted."""

    def __init__(self, message: str, status: int | None = None, attempts: int = 0):
        super().__init__(message)
        self.status = status
        self.attempts = attempts


@dataclass
class RetryPolicy:
    max_retries: int = 5
    backoff_base: float = 2.0
    max_backoff: float = 60.0
    jitter: float = 0.25

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(float(retry_after), self.max_backoff)
        d = min(self.backoff_base ** attempt, self.max_backoff)
        return d * (1 + random.uniform(-self.jitter, self.jitter))


@dataclass
class CallStats:
    attempts: int = 0
    last_status: int | None = None
    waits: list[float] = field(default_factory=list)


def call_with_retry(fn: Callable[[], T], policy: RetryPolicy, stats: CallStats | None = None,
                    sleep: Callable[[float], None] = time.sleep) -> T:
    stats = stats if stats is not None else CallStats()
    for attempt in range(policy.max_retries + 1):
        stats.attempts += 1
        try:
            result = fn()
            stats.last_status = 200
            return result
        except TransientError as exc:
            stats.last_status = exc.status
            if attempt == policy.max_retries:
                raise IngestionError(f"retries exhausted: {exc}", exc.status, stats.attempts) from exc
            wait = policy.delay(attempt, exc.retry_after)
            stats.waits.append(wait)
            sleep(wait)
    raise AssertionError("unreachable")


class RateLimitedClient:
    """requests.Session wrapper that spaces calls to respect a requests-per-minute budget."""

    def __init__(self, base_url: str, requests_per_minute: int = 25, timeout: float = 20,
                 headers: dict | None = None, session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.base_url = base_url.rstrip("/")
        self.min_interval = 60.0 / max(requests_per_minute, 1)
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": "analytics-portfolio/1.0"})
        if headers:
            self.session.headers.update(headers)
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None

    def _throttle(self) -> None:
        if self._last_call is not None:
            wait = self.min_interval - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()

    def get_json(self, path: str, params: dict | None = None):
        """Single attempt. Raises TransientError for retryable statuses."""
        self._throttle()
        try:
            resp = self.session.get(f"{self.base_url}/{path.lstrip('/')}", params=params, timeout=self.timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise TransientError(f"network error: {exc.__class__.__name__}") from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            ra = resp.headers.get("Retry-After")
            raise TransientError(f"HTTP {resp.status_code} on {path}", resp.status_code,
                                 float(ra) if ra and ra.replace(".", "", 1).isdigit() else None)
        if resp.status_code >= 400:
            raise IngestionError(f"HTTP {resp.status_code} on {path}: {resp.text[:200]}", resp.status_code)
        return resp.json()
