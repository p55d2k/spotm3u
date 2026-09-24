"""Shared positive-result cache and single-flight coordination for metadata."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Pending:
    event: threading.Event
    result: object = None


class MetadataCache:
    """Bounded cache that shares successful work while it is in flight.

    ``None`` is never retained: missing metadata and provider failures are
    retried on a later request rather than becoming permanent negative cache
    entries.
    """

    def __init__(self, limit: int = 2048) -> None:
        self._limit = max(1, int(limit))
        self._values: OrderedDict[str, object] = OrderedDict()
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()
        self.metrics = {"requests": 0, "cache_hits": 0, "singleflight_hits": 0, "cache_misses": 0}

    def get_or_fetch(self, key: str, fetch: Callable[[], object]) -> object:
        with self._lock:
            if key in self._values:
                self.metrics["cache_hits"] += 1
                value = self._values.pop(key)
                self._values[key] = value
                return value
            pending = self._pending.get(key)
            if pending is not None:
                self.metrics["singleflight_hits"] += 1
                owner = False
            else:
                pending = _Pending(threading.Event())
                self._pending[key] = pending
                self.metrics["cache_misses"] += 1
                owner = True
        if not owner:
            pending.event.wait()
            return pending.result
        try:
            self.metrics["requests"] += 1
            value = fetch()
            pending.result = value
            if value is not None:
                with self._lock:
                    self._values[key] = value
                    self._values.move_to_end(key)
                    while len(self._values) > self._limit:
                        self._values.popitem(last=False)
            return value
        finally:
            with self._lock:
                self._pending.pop(key, None)
            pending.event.set()


__all__ = ["MetadataCache"]
