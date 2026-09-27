"""Process-wide per-file locks shared by everything that writes a download.

One audio file is touched by several stages from different workers and, in a
batch, from different jobs at the same time: the downloader writes it, audio
validation reads it, and metadata enrichment rewrites its ID3 tag (and artwork
and lyrics frames). If a download (which removes and rewrites the whole file)
runs while another stage is reading or rewriting the same file, the two writes
interleave and the audio is spliced -- the track still decodes, but it repeats
or skips a section.

Keying the lock by the resolved path means the downloader, validation and
enrichment all take the *same* lock for a file, so those stages can never
overlap on it. Distinct files still run concurrently. The lock is reentrant
because a normal (non-fast) download enriches the file it just wrote inside the
same thread, which re-acquires the lock.
"""

from __future__ import annotations

import threading
from pathlib import Path

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def file_lock(path: str | Path) -> threading.RLock:
    """Return the lock shared by every stage writing one local file.

    The key is the resolved path so relative and absolute spellings of the same
    file map to one lock. Reentrant so a download can enrich the file it just
    wrote in the same thread.
    """
    key = str(Path(path).resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


__all__ = ["file_lock"]
