"""Small helpers for using yt-dlp safely from concurrent worker threads."""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# yt-dlp lazily loads plugins from ``YoutubeDL.__init__`` without holding a lock,
# and its loader re-executes plugin modules even when they are already imported.
# When several spotm3u workers construct ``YoutubeDL`` at once, plugins such as
# the bgutil PO Token providers register twice and yt-dlp reports
# "... already registered". Loading plugins once here, under a lock, keeps that
# one-time registration race-free.
_PLUGIN_LOCK = threading.Lock()

# Plugin modules are arbitrary third-party code that runs at import time, so a
# hung import (for example one blocked on the network) must not stall a worker
# forever. The whole load is bounded by this wall-clock budget.
_PLUGIN_LOAD_TIMEOUT = 30.0


def ensure_ytdlp_plugins_loaded() -> None:
    """Load yt-dlp plugins once, before any concurrent ``YoutubeDL`` construction.

    Safe to call from any number of threads and any number of times; it is a
    no-op once plugins are loaded. If the yt-dlp internals are unavailable or
    changed, it silently defers to yt-dlp's own lazy loading. A plugin load that
    hangs past its timeout is abandoned instead of stalling the caller; the stuck
    import keeps running in a daemon thread, so a later call sees plugins loaded
    if they eventually finish.
    """
    with _PLUGIN_LOCK:
        try:
            from yt_dlp.globals import all_plugins_loaded
            from yt_dlp.plugins import load_all_plugins
        except Exception:  # noqa: BLE001 - fall back to yt-dlp's own loading
            return
        if not all_plugins_loaded.value:
            _load_with_timeout(load_all_plugins)


def _load_with_timeout(load_all_plugins: object, timeout: float = _PLUGIN_LOAD_TIMEOUT) -> None:
    """Run ``load_all_plugins`` in a daemon thread bounded by ``timeout``.

    Returns before the load finishes when it exceeds ``timeout``; the daemon
    thread keeps running in the background, and yt-dlp's own lazy loading remains
    as a fallback for anything that never completes.
    """
    done = threading.Event()
    outcome: list[BaseException] = []

    def target() -> None:
        try:
            load_all_plugins()
        except BaseException as exc:  # noqa: BLE001 - report, do not propagate
            outcome.append(exc)
        finally:
            done.set()

    worker = threading.Thread(
        target=target,
        name="spotm3u-plugin-load",
        daemon=True,
    )
    worker.start()
    if done.wait(timeout):
        if outcome:
            logger.warning("yt-dlp plugin load failed: %s", outcome[0])
        return
    logger.warning(
        "yt-dlp plugin load exceeded %.0fs; deferring to yt-dlp lazy loading",
        timeout,
    )


__all__ = ["ensure_ytdlp_plugins_loaded"]
