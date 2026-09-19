"""Small helpers for using yt-dlp safely from concurrent worker threads."""

from __future__ import annotations

import threading

# yt-dlp lazily loads plugins from ``YoutubeDL.__init__`` without holding a lock,
# and its loader re-executes plugin modules even when they are already imported.
# When several spotm3u workers construct ``YoutubeDL`` at once, plugins such as
# the bgutil PO Token providers register twice and yt-dlp reports
# "... already registered". Loading plugins once here, under a lock, keeps that
# one-time registration race-free.
_PLUGIN_LOCK = threading.Lock()


def ensure_ytdlp_plugins_loaded() -> None:
    """Load yt-dlp plugins once, before any concurrent ``YoutubeDL`` construction.

    Safe to call from any number of threads and any number of times; it is a
    no-op once plugins are loaded. If the yt-dlp internals are unavailable or
    changed, it silently defers to yt-dlp's own lazy loading.
    """
    with _PLUGIN_LOCK:
        try:
            from yt_dlp.globals import all_plugins_loaded
            from yt_dlp.plugins import load_all_plugins
        except Exception:  # noqa: BLE001 - fall back to yt-dlp's own loading
            return
        if not all_plugins_loaded.value:
            load_all_plugins()


__all__ = ["ensure_ytdlp_plugins_loaded"]
