"""Conservative metadata ranking for online source candidates."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Literal

from ..models import Track
from ..normalization import normalize
from .search import SourceCandidate

Confidence = Literal["strong", "uncertain", "rejected"]

_ALTERNATE_VERSION_RE = re.compile(
    r"\b(?:official\s+music\s+video|music\s+video|video|live|concert|"
    r"remix|mashup|sped[\s-]?up|slowed|nightcore|edit|fan[\s-]?made|"
    r"extended|acoustic|cover|karaoke|instrumental|trailer|teaser|"
    r"interview|reaction|movie|film|scene|soundtrack)\b",
    re.IGNORECASE,
)
_POSITIVE_SOURCE_RE = re.compile(
    r"\b(?:official\s+audio|audio|topic|artist|records?|vevo)\b", re.IGNORECASE
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
    """Return a conservative ranking for one candidate."""
    title = _title_for_comparison(candidate.title)
    requested_title = _title_for_comparison(track.title)
    candidate_text = normalize(
        " ".join(
            str(value)
            for value in (
                candidate.title,
                candidate.artist,
                candidate.uploader,
                candidate.metadata.get("description", ""),
            )
            if value
        )
    )
    requested_artists = [normalize(artist) for artist in track.artists if normalize(artist)]
    artist_text = normalize(candidate.artist or candidate.uploader or "")
    title_similarity = _similarity(requested_title, title)
    artist_matches = sum(_similarity(artist, artist_text) >= 0.8 for artist in requested_artists)
    artist_similarity = max((_similarity(artist, artist_text) for artist in requested_artists), default=0.0)

    score = 42 * title_similarity + 28 * artist_similarity
    reasons: list[str] = []
    if title_similarity >= 0.98:
        reasons.append("title matches")
    elif title_similarity < 0.55:
        reasons.append("title mismatch")
    if artist_matches:
        score += min(12, artist_matches * 6)
        reasons.append("artist matches")
    elif requested_artists:
        reasons.append("artist mismatch")

    if track.duration_ms and candidate.duration_s is not None:
        difference = abs(candidate.duration_s - track.duration_ms / 1000)
        if difference <= 3:
            score += 15
            reasons.append("duration matches")
        elif difference <= 12:
            score += 7
            reasons.append("duration is close")
        else:
            score -= min(20, 8 + difference / 10)
            reasons.append("duration mismatch")

    if track.album and normalize(track.album) in candidate_text:
        score += 4
        reasons.append("album metadata matches")
    if _POSITIVE_SOURCE_RE.search(candidate_text):
        score += 4
        reasons.append("audio or official source indicator")

    alternate_matches = _ALTERNATE_VERSION_RE.findall(candidate_text)
    if alternate_matches:
        penalty = min(55, 18 + 8 * len(set(match.casefold() for match in alternate_matches)))
        score -= penalty
        reasons.append("alternate or unsuitable version indicator")

    score = max(0.0, min(100.0, score))
    clear_artist_mismatch = bool(requested_artists) and artist_similarity < 0.72
    unsuitable = bool(alternate_matches)
    if unsuitable or title_similarity < 0.35 or clear_artist_mismatch or score < 35:
        confidence: Confidence = "rejected"
        accepted = False
    elif score >= 78 and artist_matches:
        confidence = "strong"
        accepted = True
    else:
        confidence = "uncertain"
        accepted = False
    return CandidateRanking(candidate, round(score, 2), confidence, accepted, tuple(reasons))


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _title_for_comparison(value: str | None) -> str:
    """Ignore ordinary audio-label metadata, but retain alternate versions."""
    title = normalize(value)
    return re.sub(r"\b(?:official\s+audio|audio|official)\b", " ", title).strip()


__all__ = [
    "CandidateRanking",
    "Confidence",
    "rank_source_candidate",
    "rank_source_candidates",
]
