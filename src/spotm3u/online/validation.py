"""Conservative metadata validation for online source candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
import re

from ..models import Track
from ..normalization import normalize
from .ranking import rank_source_candidate
from .search import SourceCandidate

ValidationStatus = Literal["accepted", "rejected", "uncertain"]

_UNSUITABLE_RE = re.compile(
    r"\b(?:dialogue|movie|film|scene|trailer|interview|podcast|reaction|"
    r"compilation|cover|karaoke|remix|mashup|sped[\s-]?up|slowed|nightcore|"
    r"fan[\s-]?made|music[\s-]?video|live|concert|acoustic|instrumental)\b",
    re.IGNORECASE,
)
_UNSUITABLE_TYPES = {
    "podcast",
    "interview",
    "movie",
    "film",
    "trailer",
    "reaction",
    "compilation",
    "music video",
    "live",
}


@dataclass(frozen=True)
class SourceValidation:
    """A metadata-only decision; acceptance does not guarantee clean audio."""

    candidate: SourceCandidate
    status: ValidationStatus
    reasons: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


def validate_source_candidate(track: Track, candidate: SourceCandidate) -> SourceValidation:
    """Classify a candidate before download without claiming audio is clean."""
    text = _metadata_text(candidate)
    markers = _UNSUITABLE_RE.findall(text)
    if markers:
        return SourceValidation(
            candidate,
            "rejected",
            ("unsuitable content marker: " + ", ".join(dict.fromkeys(marker.casefold() for marker in markers)),),
        )

    source_type = normalize(candidate.source_type or candidate.metadata.get("type"))
    if source_type in _UNSUITABLE_TYPES:
        return SourceValidation(candidate, "rejected", ("unsuitable source type",))

    missing: list[str] = []
    if candidate.duration_s is None:
        missing.append("duration is missing")
    if not candidate.uploader and not candidate.artist:
        missing.append("uploader and artist metadata are missing")
    if not candidate.source_type:
        missing.append("source type is missing")

    ranking = rank_source_candidate(track, candidate)
    if ranking.confidence == "rejected":
        if missing:
            return SourceValidation(candidate, "uncertain", tuple(missing))
        return SourceValidation(candidate, "rejected", ranking.reasons)

    if missing or ranking.confidence != "strong" or _title_similarity_is_ambiguous(track, candidate):
        return SourceValidation(candidate, "uncertain", tuple(missing) or ranking.reasons)
    return SourceValidation(candidate, "accepted", ranking.reasons)


def validate_source_candidates(
    track: Track, candidates: list[SourceCandidate] | tuple[SourceCandidate, ...]
) -> tuple[SourceValidation, ...]:
    """Validate candidates while preserving their input order."""
    return tuple(validate_source_candidate(track, candidate) for candidate in candidates)


def _metadata_text(candidate: SourceCandidate) -> str:
    values = (
        candidate.title,
        candidate.artist,
        candidate.uploader,
        candidate.source_type,
        candidate.metadata.get("description"),
        candidate.metadata.get("category"),
        candidate.metadata.get("genre"),
    )
    return " ".join(str(value) for value in values if value)


def _title_similarity_is_ambiguous(track: Track, candidate: SourceCandidate) -> bool:
    requested = normalize(track.title)
    title = normalize(candidate.title)
    return bool(requested and title and not _contains_title(requested, title))


def _contains_title(requested: str, candidate: str) -> bool:
    requested_tokens = set(requested.split())
    candidate_tokens = set(candidate.split())
    return bool(requested_tokens) and requested_tokens <= candidate_tokens


__all__ = [
    "SourceValidation",
    "ValidationStatus",
    "validate_source_candidate",
    "validate_source_candidates",
]
