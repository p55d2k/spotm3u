"""Developer maintenance: remove what SpotM3U wrote, in selectable parts.

The download folder, the caches beside it and the temporary upload folders are
all disposable, but they are also the only record of a conversion, so they are
cleared by choice rather than in one sweep: the caller picks the items and gets
back what each of them actually removed. :func:`inventory` answers the same
question without touching anything, which is what the developer panel shows
next to its checkboxes.

Only what this application writes is ever in reach: the MP3s and M3U playlists
directly inside the download folder, the download manifest, and the two cache
folders created inside that same folder. Nothing recurses out of them and no
link is followed, so a hand-placed file or a symlink into the wider music
library is never removed. A download folder that resolves to the music library
itself, or to a folder above it, is refused outright rather than trusted.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from .artwork_cache import artwork_cache_dir, forget_artwork_memory
from .metadata import lyrics_cache_dir
from .online.cache import MANIFEST_NAME
from .uploads import JOB_DIR_PREFIX

# The selectable items, in the order the panel lists them. Songs and playlists
# are the user's own files, so they come first; the rest is rebuildable.
CLEAR_ITEMS: tuple[str, ...] = (
    "songs",
    "playlists",
    "manifest",
    "artwork",
    "lyrics",
    "uploads",
)

# The phrase a caller has to send. It is not a secret -- the API is
# unauthenticated and loopback-only -- it is a deliberate act, one that survives
# a stray retry or a replayed request.
CONFIRM_PHRASE = "delete"

_SONG_SUFFIXES = frozenset({".mp3"})
_PLAYLIST_SUFFIXES = frozenset({".m3u"})


class UnsafeTarget(Exception):
    """A clear was refused because it would reach outside the download folder."""


def inventory(download_dir: str | Path, upload_root: str | Path) -> dict[str, object]:
    """What a clear would remove right now, per selectable item.

    Counts and sizes are best effort: a file that cannot be measured or listed
    is treated as absent rather than failing the whole inventory, so the panel
    still renders on a library that is being written to.
    """
    download = Path(download_dir)
    upload = Path(upload_root)
    songs = _own_files(download, _SONG_SUFFIXES)
    playlists = _own_files(download, _PLAYLIST_SUFFIXES)
    return {
        "download_dir": str(download),
        "confirm_phrase": CONFIRM_PHRASE,
        "items": [
            _item("songs", *_measure_files(songs)),
            _item("playlists", *_measure_files(playlists)),
            _item("manifest", *_measure_file(download / MANIFEST_NAME)),
            _item("artwork", *_measure_tree(artwork_cache_dir(download))),
            _item("lyrics", *_measure_tree(lyrics_cache_dir(download))),
            _item("uploads", *_measure_trees(_job_directories(upload))),
        ],
    }


def clear(
    download_dir: str | Path,
    upload_root: str | Path,
    items: Iterable[str],
    *,
    music_library: str | Path | None = None,
) -> dict[str, object]:
    """Delete the named items from disk and report what each one removed.

    ``items`` is checked against :data:`CLEAR_ITEMS` by the caller, which is
    the HTTP layer; an unknown name is therefore never silently ignored here.
    Removal is best effort per file: one file that cannot be deleted (a busy
    player, a read-only folder) does not stop the rest, and the report counts
    only what is actually gone.
    """
    download = Path(download_dir)
    refused = _refusal_reason(download, music_library)
    if refused is not None:
        raise UnsafeTarget(refused)
    upload = Path(upload_root)
    selected = {str(item) for item in items}
    removed: dict[str, int] = {}
    bytes_freed = 0
    for name in CLEAR_ITEMS:
        if name not in selected:
            continue
        count, size = _REMOVERS[name](download, upload)
        removed[name] = count
        bytes_freed += size
    return {"download_dir": str(download), "removed": removed, "bytes_freed": bytes_freed}


def unknown_items(items: Sequence[str]) -> list[str]:
    """The requested item names that are not selectable, sorted for a message."""
    return sorted({str(item) for item in items} - set(CLEAR_ITEMS))


def _refusal_reason(download_dir: Path, music_library: str | Path | None) -> str | None:
    """Why this download folder must not be emptied, or ``None`` when it is safe.

    The removers below are already limited to the files SpotM3U writes, so a
    misconfigured ``download_dir`` could only ever lose downloads of its own.
    It is still refused: a folder that *is* the music library would take the
    user's own music into the blast radius of a mis-click, and no file there was
    ever ours to delete.
    """
    try:
        resolved = download_dir.resolve()
    except OSError:
        return None
    if resolved == Path(resolved.anchor) or resolved == Path.home():
        return "The download folder is the filesystem root or your home folder."
    if music_library is None:
        return None
    try:
        library = Path(music_library).resolve()
    except OSError:
        return None
    if resolved == library:
        return "The download folder is the music library itself."
    if library.is_relative_to(resolved):
        return "The download folder contains the music library."
    return None


def _own_files(directory: Path, suffixes: frozenset[str]) -> list[Path]:
    """The regular files directly in ``directory`` with one of ``suffixes``.

    Not recursive, and a symlink is never returned: the songs and playlists
    SpotM3U writes sit at the top of its own download folder, and a link placed
    there points at something this application did not create.
    """
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return []
    return [
        entry
        for entry in entries
        if entry.suffix.casefold() in suffixes and not entry.is_symlink() and entry.is_file()
    ]


def _job_directories(upload_root: Path) -> list[Path]:
    """The temporary upload job directories, newest name order for a stable report."""
    try:
        entries = sorted(upload_root.iterdir())
    except OSError:
        return []
    return [
        entry
        for entry in entries
        if entry.is_dir() and not entry.is_symlink() and entry.name.startswith(JOB_DIR_PREFIX)
    ]


def _item(name: str, files: int, size: int) -> dict[str, object]:
    """One inventory row: what the item holds right now."""
    return {"name": name, "files": files, "bytes": size}


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _measure_files(paths: Iterable[Path]) -> tuple[int, int]:
    """How many files ``paths`` holds and what they weigh."""
    files = 0
    size = 0
    for path in paths:
        files += 1
        size += _size(path)
    return files, size


def _measure_file(path: Path) -> tuple[int, int]:
    """One file as ``(files, bytes)``, or ``(0, 0)`` when it is not there."""
    if path.is_symlink() or not path.is_file():
        return 0, 0
    return 1, _size(path)


def _measure_tree(directory: Path) -> tuple[int, int]:
    """How many files a cache folder holds and what they weigh."""
    if directory.is_symlink() or not directory.is_dir():
        return 0, 0
    files = 0
    size = 0
    for child in directory.rglob("*"):
        if child.is_symlink() or not child.is_file():
            continue
        files += 1
        size += _size(child)
    return files, size


def _measure_trees(directories: Iterable[Path]) -> tuple[int, int]:
    """The summed measurement of several folders."""
    files = 0
    size = 0
    for directory in directories:
        measured, weight = _measure_tree(directory)
        files += measured
        size += weight
    return files, size


def _remove_files(paths: Iterable[Path]) -> tuple[int, int]:
    """Delete the given files, counting the ones that are actually gone."""
    files = 0
    size = 0
    for path in paths:
        try:
            weight = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        files += 1
        size += weight
    return files, size


def _remove_file(path: Path) -> tuple[int, int]:
    return _remove_files([path]) if path.is_file() and not path.is_symlink() else (0, 0)


def _remove_tree(directory: Path) -> tuple[int, int]:
    """Delete one cache folder whole; it belongs to SpotM3U and is rebuilt on demand."""
    files, size = _measure_tree(directory)
    if files or directory.is_dir():
        shutil.rmtree(directory, ignore_errors=True)
    return files, size


def _remove_songs(download_dir: Path, upload_root: Path) -> tuple[int, int]:
    return _remove_files(_own_files(download_dir, _SONG_SUFFIXES))


def _remove_playlists(download_dir: Path, upload_root: Path) -> tuple[int, int]:
    return _remove_files(_own_files(download_dir, _PLAYLIST_SUFFIXES))


def _remove_manifest(download_dir: Path, upload_root: Path) -> tuple[int, int]:
    return _remove_file(download_dir / MANIFEST_NAME)


def _remove_artwork(download_dir: Path, upload_root: Path) -> tuple[int, int]:
    # The in-process memo still holds the images just deleted from disk, so it
    # is emptied too; otherwise this process would keep serving artwork the
    # cache no longer has.
    forget_artwork_memory(download_dir)
    return _remove_tree(artwork_cache_dir(download_dir))


def _remove_lyrics(download_dir: Path, upload_root: Path) -> tuple[int, int]:
    return _remove_tree(lyrics_cache_dir(download_dir))


def _remove_trees(directories: Iterable[Path]) -> tuple[int, int]:
    files = 0
    size = 0
    for directory in directories:
        measured, weight = _remove_tree(directory)
        files += measured
        size += weight
    return files, size


def _remove_uploads(download_dir: Path, upload_root: Path) -> tuple[int, int]:
    return _remove_trees(_job_directories(upload_root))


_REMOVERS: dict[str, Callable[[Path, Path], tuple[int, int]]] = {
    "songs": _remove_songs,
    "playlists": _remove_playlists,
    "manifest": _remove_manifest,
    "artwork": _remove_artwork,
    "lyrics": _remove_lyrics,
    "uploads": _remove_uploads,
}


__all__ = [
    "CLEAR_ITEMS",
    "CONFIRM_PHRASE",
    "UnsafeTarget",
    "clear",
    "inventory",
    "unknown_items",
]
