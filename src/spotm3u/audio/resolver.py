"""Resolve generic tracks against a local audio library."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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

# Threshold used by ``resolve`` when deciding whether a fuzzy match is usable.
_FUZZY_SCORE_CUTOFF = 0.72

# Script ranges treated as one-character search keys. CJK song titles are often
# short (1-2 characters) and appear embedded in longer filenames, so single CJK
# characters are indexed individually instead of only as bigram pairs.
_CJK_RANGES = (
    (0x2E80, 0x2FDF),  # radicals, CJK punctuation
    (0x3040, 0x30FF),  # hiragana, katakana
    (0x3400, 0x4DBF),  # unified ideographs extension A
    (0x4E00, 0x9FFF),  # unified ideographs
    (0xAC00, 0xD7AF),  # hangul syllables
    (0xF900, 0xFAFF),  # compatibility ideographs
    (0x20000, 0x2EBEF),  # unified ideographs extension B+
)


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return any(start <= code <= end for start, end in _CJK_RANGES)


def _grams(value: str):
    """Yield adjacency bigrams plus lone CJK characters.

    Bigrams catch fuzzy/typo'd matches ("wonderwal" and "wonderwall" share
    several bigrams), while the CJK unigrams let a one-character title such as
    ``愿`` match a filename that contains it inside a longer word.
    """
    if not value:
        return
    prev = value[0]
    for char in value[1:]:
        yield prev + char
        prev = char
    for char in value:
        if _is_cjk(char):
            yield char


def _length_compatible(left: str, right: str, cutoff: float = _FUZZY_SCORE_CUTOFF) -> bool:
    """True when two strings could possibly reach ``cutoff`` via fuzzy ratio.

    The Levenshtein-style ratio is bounded by ``2 * min(L1, L2) / (L1 + L2)``
    because the edit distance is never smaller than the length difference. If
    that upper bound is below the cutoff the fuzzy score cannot reach it, so
    the expensive comparison can be skipped without changing results.
    """
    if not left or not right:
        return False
    if left == right:
        return True
    smaller, larger = (left, right) if len(left) <= len(right) else (right, left)
    return 2.0 * len(smaller) / (len(smaller) + len(larger)) >= cutoff


def _fuzzy_ratio(lhs: str, rhs: str) -> float:
    if not lhs or not rhs:
        return 0.0
    if rapidfuzz_fuzz is not None:
        return rapidfuzz_fuzz.ratio(lhs, rhs) / 100
    return SequenceMatcher(None, lhs, rhs).ratio()


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
        self._stems: tuple[str, ...] = ()
        self._keys: tuple[tuple[str, ...], ...] = ()
        self._by_key: dict[str, list[int]] = {}
        self._token_index: dict[str, frozenset[int]] = {}
        self._bigram_index: dict[str, frozenset[int]] = {}
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the library index."""
        if not self.root.is_dir():
            self._files = ()
            self._stems = ()
            self._keys = ()
            self._by_key = {}
            self._token_index = {}
            self._bigram_index = {}
            return

        files: list[Path] = []
        stems: list[str] = []
        keys: list[tuple[str, ...]] = []
        by_key: dict[str, list[int]] = {}
        token_index: dict[str, set[int]] = {}
        bigram_index: dict[str, set[int]] = {}

        audio_paths = (
            path
            for path in sorted(self.root.rglob("*"))
            if path.is_file() and path.suffix.lower() in self.extensions
        )
        for path in audio_paths:
            stem = filename_stem(path)
            file_keys = filename_keys(path)
            index = len(files)
            files.append(path)
            stems.append(stem)
            keys.append(file_keys)
            for key in file_keys:
                by_key.setdefault(key, []).append(index)
            for token in stem.split():
                token_index.setdefault(token, set()).add(index)
            for gram in _grams(stem):
                bigram_index.setdefault(gram, set()).add(index)

        self._files = tuple(files)
        self._stems = tuple(stems)
        self._keys = tuple(keys)
        self._by_key = by_key
        self._token_index = {token: frozenset(indices) for token, indices in token_index.items()}
        self._bigram_index = {gram: frozenset(indices) for gram, indices in bigram_index.items()}

    def _match_candidates(self, track: Track) -> tuple[Path, ...]:
        title_key = normalize(track.title)
        if not title_key:
            return ()
        artist_key = normalize(" ".join(track.artists))

        candidates: list[int] = []
        seen: set[int] = set()
        for key in [
            track_key(track.title, track.artists),
            f"{artist_key} {title_key}".strip(),
            title_key,
        ]:
            for index in self._by_key.get(key, ()):
                if index not in seen:
                    seen.add(index)
                    candidates.append(index)

        for index, stem in enumerate(self._stems):
            if index in seen:
                continue
            if (title_key in stem) or (artist_key and artist_key in stem):
                if index not in seen:
                    seen.add(index)
                    candidates.append(index)
        return tuple(self._files[index] for index in candidates)

    def _fuzzy_candidates(self, title_key: str, artist_key: str) -> frozenset[int]:
        """Return a superset of files the previous scorer could practically match.

        Exact filename-key hits are handled by ``_match_candidates``; this pass
        covers substring, token, and character-adjacency matches so the fuzzy
        scoring loop only visits files that are genuinely related to the title
        or artist instead of rescanning the whole library per track.
        """
        indices: set[int] = set()

        tokens = []
        if title_key:
            tokens.extend(title_key.split())
        if artist_key:
            tokens.extend(artist_key.split())
        for token in tokens:
            indices.update(self._token_index.get(token, ()))

        grams = set(_grams(title_key)) if title_key else set()
        if artist_key:
            grams.update(_grams(artist_key))
        for gram in grams:
            indices.update(self._bigram_index.get(gram, ()))

        for index, stem in enumerate(self._stems):
            if (title_key and title_key in stem) or (artist_key and artist_key in stem):
                indices.add(index)

        return frozenset(indices)

    def _score(self, title_key: str, artist_key: str, query: str, index: int) -> float:
        stem = self._stems[index]
        score = 0.0

        for key in self._keys[index]:
            if key == query:
                score = max(score, 1.0)
            elif title_key == key or f"{artist_key} {title_key}".strip() == key:
                score = max(score, 0.95)
        if title_key and title_key in stem:
            score = max(score, 0.9)
        if artist_key and artist_key in stem:
            score = max(score, 0.8)
        if stem:
            for target in (query, title_key, artist_key):
                if target and _length_compatible(target, stem):
                    score = max(score, _fuzzy_ratio(target, stem))
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

        artist_key = normalize(" ".join(track.artists))
        query = track_key(track.title, track.artists)
        scored = sorted(
            (
                (self._score(title_key, artist_key, query, index), self._files[index])
                for index in sorted(self._fuzzy_candidates(title_key, artist_key))
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        if not scored:
            return Resolution(track, None)

        best_score = scored[0][0]
        if best_score < _FUZZY_SCORE_CUTOFF:
            return Resolution(track, None)

        tied = tuple(path for score, path in scored if score >= best_score - 0.02)
        if len(tied) > 1:
            return Resolution(track, None, tied)
        chosen = tied[0]
        return Resolution(track, ResolvedTrack(track, chosen), tied)

    def resolve_all(self, tracks: list[Track], *, max_workers: int = 1) -> list[Resolution]:
        """Resolve tracks in source order, preserving duplicates.

        With ``max_workers > 1`` tracks are matched in parallel; every slot is
        still resolved and returned in source order.
        """
        if max_workers <= 1 or len(tracks) <= 1:
            return [self.resolve(track) for track in tracks]
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            return list(pool.map(self.resolve, tracks))
