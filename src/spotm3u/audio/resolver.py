"""Resolve generic tracks against a local audio library."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from ..models import ResolvedTrack, Track
from ..normalization import filename_stem, normalize, track_key

AUDIO_EXTENSIONS = frozenset({".mp3", ".m4a", ".flac", ".wav", ".aac", ".ogg", ".webm"})


@dataclass(frozen=True)
class Resolution:
    """The result of resolving one track."""

    track: Track
    resolved: ResolvedTrack | None
    candidates: tuple[Path, ...] = ()

    @property
    def status(self) -> str:
        if self.resolved is not None:
            return "matched"
        return "ambiguous" if self.candidates else "missing"


class LocalAudioResolver:
    """Index a music directory once and resolve tracks without user interaction."""

    def __init__(self, root: str | Path, *, extensions: set[str] | None = None) -> None:
        self.root = Path(root).expanduser()
        self.extensions = frozenset(
            extension.lower() if extension.startswith(".") else f".{extension.lower()}"
            for extension in (extensions or AUDIO_EXTENSIONS)
        )
        self._files: tuple[Path, ...] = ()
        self._by_key: dict[str, list[Path]] = {}
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the library index."""
        if not self.root.is_dir():
            self._files = ()
            self._by_key = {}
            return
        self._files = tuple(
            path for path in sorted(self.root.rglob("*"))
            if path.is_file() and path.suffix.lower() in self.extensions
        )
        index: dict[str, list[Path]] = {}
        for path in self._files:
            index.setdefault(filename_stem(path), []).append(path)
        self._by_key = index

    def resolve(self, track: Track) -> Resolution:
        """Resolve one track, refusing to choose between equally plausible files."""
        title_key = normalize(track.title)
        if not title_key:
            return Resolution(track, None)
        artist_key = normalize(" ".join(track.artists))
        exact_keys = [
            track_key(track.title, track.artists),
            f"{artist_key} {title_key}".strip(),
            title_key,
        ]
        for key in exact_keys:
            candidates = self._by_key.get(key, [])
            if len(candidates) == 1:
                return Resolution(track, ResolvedTrack(track, candidates[0]), tuple(candidates))
            if len(candidates) > 1:
                return Resolution(track, None, tuple(candidates))

        query = track_key(track.title, track.artists)
        scored = sorted(
            (
                (SequenceMatcher(None, query, filename_stem(path)).ratio(), path)
                for path in self._files
                if title_key in filename_stem(path)
            ),
            reverse=True,
        )
        if not scored:
            return Resolution(track, None)
        best_score = scored[0][0]
        tied = tuple(path for score, path in scored if score >= best_score - 0.02)
        if best_score >= 0.72 and len(tied) == 1:
            return Resolution(track, ResolvedTrack(track, tied[0]), tied)
        return Resolution(track, None, tied)

    def resolve_all(self, tracks: list[Track]) -> list[Resolution]:
        """Resolve tracks in source order, preserving duplicates."""
        return [self.resolve(track) for track in tracks]
