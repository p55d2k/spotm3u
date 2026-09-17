"""Resolve generic tracks against a local audio library."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

try:
    from rapidfuzz import fuzz as rapidfuzz_fuzz
except ImportError:  # pragma: no cover - optional dependency
    rapidfuzz_fuzz = None

from ..models import ResolvedTrack, Track
from ..normalization import filename_keys, filename_stem, normalize, track_key

AUDIO_EXTENSIONS = frozenset(
    {
        ".aac",
        ".aiff",
        ".alac",
        ".flac",
        ".m4a",
        ".mp2",
        ".mp3",
        ".ogg",
        ".opus",
        ".wav",
        ".wma",
    }
)


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
            for key in filename_keys(path):
                index.setdefault(key, []).append(path)
        self._by_key = index

    @staticmethod
    def _dedupe(paths: list[Path] | tuple[Path, ...]) -> tuple[Path, ...]:
        seen: set[Path] = set()
        ordered: list[Path] = []
        for path in paths:
            if path not in seen:
                seen.add(path)
                ordered.append(path)
        return tuple(ordered)

    def _match_candidates(self, track: Track) -> tuple[Path, ...]:
        title_key = normalize(track.title)
        if not title_key:
            return ()
        artist_key = normalize(" ".join(track.artists))
        candidates: list[Path] = []
        for key in [
            track_key(track.title, track.artists),
            f"{artist_key} {title_key}".strip(),
            title_key,
        ]:
            candidates.extend(self._by_key.get(key, []))

        for path in self._files:
            stem = filename_stem(path)
            if title_key in stem or artist_key in stem:
                candidates.append(path)
        return self._dedupe(candidates)

    @staticmethod
    def _score_candidate(track: Track, path: Path) -> float:
        title_key = normalize(track.title)
        artist_key = normalize(" ".join(track.artists))
        stem = filename_stem(path)
        query = track_key(track.title, track.artists)
        if not title_key:
            return 0.0

        def fuzzy_ratio(lhs: str, rhs: str) -> float:
            if not lhs or not rhs:
                return 0.0
            if rapidfuzz_fuzz is not None:
                return rapidfuzz_fuzz.ratio(lhs, rhs) / 100
            return SequenceMatcher(None, lhs, rhs).ratio()

        score = 0.0
        for key in filename_keys(path):
            if key == query:
                score = max(score, 1.0)
            elif title_key == key or f"{artist_key} {title_key}".strip() == key:
                score = max(score, 0.95)
        if title_key and title_key in stem:
            score = max(score, 0.9)
        if artist_key and artist_key in stem:
            score = max(score, 0.8)
        if stem:
            score = max(score, fuzzy_ratio(query, stem))
        if title_key and stem:
            score = max(score, fuzzy_ratio(title_key, stem))
        if artist_key and stem:
            score = max(score, fuzzy_ratio(artist_key, stem))
        return score

    def resolve(self, track: Track) -> Resolution:
        """Resolve one track, refusing to choose between equally plausible files."""
        title_key = normalize(track.title)
        if not title_key:
            return Resolution(track, None)

        exact_matches = self._match_candidates(track)
        if len(exact_matches) == 1:
            chosen = exact_matches[0]
            return Resolution(track, ResolvedTrack(track, chosen), exact_matches)
        if len(exact_matches) > 1:
            return Resolution(track, None, exact_matches)

        scored = sorted(
            (
                (self._score_candidate(track, path), path)
                for path in self._files
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        if not scored:
            return Resolution(track, None)

        best_score = scored[0][0]
        if best_score < 0.72:
            return Resolution(track, None)

        tied = tuple(path for score, path in scored if score >= best_score - 0.02)
        if len(tied) > 1:
            return Resolution(track, None, tied)
        chosen = tied[0]
        return Resolution(track, ResolvedTrack(track, chosen), tied)

    def resolve_all(self, tracks: list[Track]) -> list[Resolution]:
        """Resolve tracks in source order, preserving duplicates."""
        return [self.resolve(track) for track in tracks]
