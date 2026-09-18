"""Permissive metadata ranking for online source candidates.

Discovery is intentionally lenient: candidates with imperfect or missing
metadata may still be plausible and worth downloading. Strong evidence of the
wrong recording (wrong song, wrong artist, explicit covers/remixes, version
conflicts, obvious non-music content) is what triggers rejection.

Artist identity is a first-class signal: for common titles a candidate with a
conflicting artist must be rejected even when the title matches exactly, and a
candidate whose artist identity is confirmed outranks same-title candidates
that only happen to share the song name.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import logging
import re
from typing import Literal

from ..models import Track
from ..normalization import normalize, normalize_artists
from .search import SourceCandidate

logger = logging.getLogger(__name__)

Confidence = Literal["strong", "plausible", "uncertain", "rejected"]

_AUDIO_LABEL_RE = re.compile(
    r"\b(?:official\s+audio|official\s+music\s+videos?|official\s+video|"
    r"official\s*mv\b|official|\bmv\b|audio|lyric\s+video|lyrics)\b",
    re.IGNORECASE,
)
_REMASTER_RE = re.compile(r"\bremaster(?:ed)?\b", re.IGNORECASE)
_VERSION_KEYS = (
    "live",
    "concert",
    "acoustic",
    "instrumental",
    "radio edit",
    "extended",
    "deluxe",
    "single version",
    "demo",
    "reprise",
)
_CONFLICT_MARKERS = frozenset(_VERSION_KEYS)
_ALT_CONTENT_RE = re.compile(
    r"\b(?:cover|翻唱|karaoke|remix|mashup|parody|sped[\s-]*up|slowed|nightcore|"
    r"fan\s+made|fan\s+edit)\b",
    re.IGNORECASE,
)
_NON_MUSIC_RE = re.compile(
    r"\b(?:dialogue|movie|film|scene|trailer|teaser|interview|podcast|"
    r"reaction|compilation|documentary)\b",
    re.IGNORECASE,
)
_MUSIC_VIDEO_RE = re.compile(
    r"\b(?:music\s+video|official\s+music\s+videos?|\bmv\b|lyric\s+video)\b",
    re.IGNORECASE,
)
_POSITIVE_SOURCE_RE = re.compile(
    r"\b(?:official\s+audio|official|audio|topic|artist|records?|vevo)\b",
    re.IGNORECASE,
)
_VERSION_KEY_PATTERNS = tuple(
    (key, re.compile(rf"\b{re.escape(key)}\b", re.IGNORECASE)) for key in _VERSION_KEYS
)


@dataclass(frozen=True)
class CandidateRanking:
    """A candidate's score and decision, without accepting it implicitly."""

    candidate: SourceCandidate
    score: float
    confidence: Confidence
    accepted: bool
    reasons: tuple[str, ...] = ()


def rank_source_candidates(
    track: Track, candidates: list[SourceCandidate] | tuple[SourceCandidate, ...]
) -> tuple[CandidateRanking, ...]:
    """Rank candidates from most to least plausible for ``track``."""
    return tuple(
        sorted(
            (rank_source_candidate(track, candidate) for candidate in candidates),
            key=lambda result: result.score,
            reverse=True,
        )
    )


def rank_source_candidate(track: Track, candidate: SourceCandidate) -> CandidateRanking:
    """Return a permissive ranking where artist identity is a first-class signal.

    Artist identity is examined across the explicit artist metadata, the title
    attribution, and the uploader/channel. Confirmed identity outranks a mere
    exact title match, and a conflicting explicit artist rejects the candidate
    even when the title is identical (common song titles such as ``演员``).
    """
    requested_core, requested_versions = _split_title(track.title)
    candidate_core, candidate_versions = _split_title(candidate.title)
    candidate_text = _candidate_text(candidate)
    requested_artists = [
        normalize_artists(artist)
        for artist in track.artists
        if normalize_artists(artist)
    ]
    candidate_artist_text = normalize_artists(candidate.artist) if candidate.artist else ""
    uploader_text = normalize_artists(candidate.uploader) if candidate.uploader else ""
    title_text = normalize(candidate.title)

    title_similarity = _similarity(requested_core, candidate_core)
    artist_in_explicit = _artist_present(requested_artists, candidate_artist_text)
    artist_in_title = _artist_present(requested_artists, title_text)
    artist_in_uploader = _artist_present(requested_artists, uploader_text)

    if artist_in_explicit or artist_in_title:
        artist_evidence = 1.0
    elif artist_in_uploader:
        artist_evidence = 0.7
    else:
        artist_evidence = 0.0

    reasons: list[str] = []
    if title_similarity >= 0.98:
        reasons.append("title matches")
    elif title_similarity < 0.45:
        reasons.append("title mismatch")

    if artist_in_explicit:
        reasons.append("artist matches (explicit metadata)")
    elif artist_in_title:
        reasons.append("artist matches (title attribution)")
    elif artist_in_uploader:
        reasons.append("artist matches (uploader/channel)")
    elif requested_artists:
        if candidate_artist_text:
            reasons.append("artist mismatch")
        elif uploader_text:
            reasons.append("uploader differs (artist identity not confirmed)")
        else:
            reasons.append("artist identity not confirmed")

    score = 45 * title_similarity + 40 * artist_evidence

    if track.duration_ms and candidate.duration_s is not None:
        difference = abs(candidate.duration_s - track.duration_ms / 1000)
        if difference <= 5:
            score += 12
            reasons.append("duration matches")
        elif difference <= 15:
            score += 7
            reasons.append("duration is close")
        elif difference <= 40:
            score += 3
        else:
            score -= 20
            reasons.append("duration differs substantially")
    elif candidate.duration_s is None:
        reasons.append("duration is unavailable")

    if track.album and normalize(track.album) in candidate_text:
        score += 4
        reasons.append("album metadata matches")
    if _POSITIVE_SOURCE_RE.search(candidate_text):
        score += 5
        reasons.append("audio or official source indicator")

    music_video = bool(_MUSIC_VIDEO_RE.search(candidate_text))
    if music_video:
        score -= 12
        reasons.append("music video indicator")

    non_music_markers = _unique_markers(_NON_MUSIC_RE, candidate_text)
    if non_music_markers:
        reasons.append("non-music content indicator: " + ", ".join(non_music_markers))
    alt_markers = _unique_markers(_ALT_CONTENT_RE, candidate_text)
    if alt_markers:
        reasons.append("alternate version indicator: " + ", ".join(alt_markers))
    conflict = _version_conflict(requested_versions, candidate_versions)
    if conflict:
        reasons.append(f"version conflict: {conflict}")

    score = max(0.0, min(100.0, score))

    alternate_wrong = bool(non_music_markers or alt_markers)
    version_wrong = conflict is not None
    explicit_conflict = bool(
        candidate_artist_text
        and not artist_in_explicit
        and not artist_in_title
        and title_similarity >= 0.5
    )
    if explicit_conflict:
        reasons.append("explicit artist conflicts with requested artist")
    duration_wrong = bool(
        track.duration_ms
        and candidate.duration_s is not None
        and abs(candidate.duration_s - track.duration_ms / 1000) > 60
    )
    weak_evidence = title_similarity < 0.35 or (
        title_similarity < 0.5 and score < 30
    )

    if (
        weak_evidence
        or explicit_conflict
        or alternate_wrong
        or version_wrong
        or duration_wrong
    ):
        confidence: Confidence = "rejected"
        accepted = False
    elif (
        title_similarity >= 0.85
        and artist_evidence >= 0.7
        and score >= 70
        and not non_music_markers
        and not music_video
    ):
        confidence = "strong"
        accepted = True
    elif not non_music_markers and not alternate_wrong and not version_wrong:
        confidence = "plausible"
        accepted = True
    else:
        confidence = "uncertain"
        accepted = False

    logger.debug(
        "rank url=%s confidence=%s accepted=%s score=%.2f title=%.2f "
        "artist_evidence=%.2f (explicit=%s title=%s uploader=%s) reasons=%s",
        candidate.url,
        confidence,
        accepted,
        score,
        title_similarity,
        artist_evidence,
        artist_in_explicit,
        artist_in_title,
        artist_in_uploader,
        "; ".join(reasons),
    )
    return CandidateRanking(candidate, round(score, 2), confidence, accepted, tuple(reasons))


def _split_title(value: str | None) -> tuple[str, frozenset[str]]:
    """Return (core title, version markers) with version info removed from core."""
    if not value:
        return "", frozenset()
    title = _AUDIO_LABEL_RE.sub(" ", normalize(value))
    versions: set[str] = set()
    for key, pattern in _VERSION_KEY_PATTERNS:
        if pattern.search(title):
            versions.add(key)
    if _REMASTER_RE.search(title):
        versions.add("remastered")
    for key, pattern in _VERSION_KEY_PATTERNS:
        title = pattern.sub(" ", title)
    title = _REMASTER_RE.sub(" ", title)
    return _collapse(title), frozenset(versions)


def _version_conflict(
    requested: frozenset[str], candidate: frozenset[str]
) -> str | None:
    conflicting = (candidate & _CONFLICT_MARKERS) - requested
    return min(conflicting) if conflicting else None


def _candidate_text(candidate: SourceCandidate) -> str:
    values = (
        candidate.title,
        candidate.artist,
        candidate.uploader,
        candidate.source_type,
        candidate.metadata.get("description"),
        candidate.metadata.get("category"),
        candidate.metadata.get("genre"),
    )
    return normalize(" ".join(str(value) for value in values if value))


def _unique_markers(pattern: re.Pattern, text: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            match for match in pattern.findall(text)
        )
    )


def _artist_present(keys: list[str], text: str) -> bool:
    """True when a requested artist identity appears in ``text``.

    Substring matching handles CJK artist names and channel suffixes such as
    ``薛之谦官方频道`` or ``Oasis - Topic`` without requiring exact equality.
    """
    if not keys or not text:
        return False
    return any(key in text for key in keys)


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _collapse(value: str) -> str:
    return " ".join(value.split())


__all__ = [
    "CandidateRanking",
    "Confidence",
    "rank_source_candidate",
    "rank_source_candidates",
]