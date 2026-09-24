"""Shared provider throttling and bounded retry handling."""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "musicbrainz": (1, 1.0),
    "coverartarchive": (2, 2.0),
    "itunes": (2, 4.0),
    "deezer": (2, 4.0),
    "lyrics": (3, 3.0),
}


class ProviderLimiter:
    """Coordinate concurrency, request spacing, retries, and basic metrics."""

    def __init__(self, concurrency: int, requests_per_second: float) -> None:
        self._slots = threading.BoundedSemaphore(max(1, concurrency))
        self._interval = 1 / max(0.01, requests_per_second)
        self._lock = threading.Lock()
        self._next_request = 0.0
        self.metrics = defaultdict(int)

    def request(self, call: Callable[[], Any], *, retries: int = 2) -> Any:
        with self._slots:
            for attempt in range(retries + 1):
                with self._lock:
                    delay = max(0.0, self._next_request - time.monotonic())
                    self._next_request = max(time.monotonic(), self._next_request) + self._interval
                if delay:
                    time.sleep(delay)
                started = time.monotonic()
                try:
                    response = call()
                    self.metrics["requests"] += 1
                    if getattr(response, "status_code", 0) == 429:
                        self.metrics["rate_limited"] += 1
                        if attempt >= retries:
                            return response
                        wait = _retry_after(response) or 2**attempt
                        self.metrics["retries"] += 1
                        time.sleep(wait)
                        continue
                    if getattr(response, "status_code", 0) >= 500 and attempt < retries:
                        self.metrics["retries"] += 1
                        time.sleep(2**attempt)
                        continue
                    self.metrics[
                        "successful" if getattr(response, "status_code", 0) < 400 else "failures"
                    ] += 1
                    return response
                except requests.RequestException:
                    self.metrics["failures"] += 1
                    if attempt >= retries:
                        raise
                    self.metrics["retries"] += 1
                    time.sleep(2**attempt)
                finally:
                    self.metrics["latency_ms"] += int((time.monotonic() - started) * 1000)
        raise RuntimeError("provider request exhausted retries")


def _retry_after(response: Any) -> float | None:
    value = response.headers.get("Retry-After") if hasattr(response, "headers") else None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


_LIMITERS = {name: ProviderLimiter(*values) for name, values in _DEFAULTS.items()}


def provider_request(provider: str, call: Callable[[], Any]) -> Any:
    """Run one external request through the provider's shared limiter."""
    return _LIMITERS[provider].request(call)


__all__ = ["ProviderLimiter", "provider_request"]
