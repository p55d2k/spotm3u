"""Metadata validation for online source candidates.

Validation is deliberately permissive. It rejects candidates only when there is
strong evidence that the source is wrong or unsuitable. Missing or imperfect
metadata is not treated as evidence of incorrectness; the downloaded audio is
validated separately afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..models import Track
from .ranking import rank_source_candidate
from .search import SourceCandidate

ValidationStatus = Literal["accepted", "rejected", "uncertain"]


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
    ranking = rank_source_candidate(track, candidate)
    if ranking.confidence == "rejected":
        return SourceValidation(candidate, "rejected", ranking.reasons)
    if ranking.confidence == "uncertain":
        return SourceValidation(candidate, "uncertain", ranking.reasons)
    return SourceValidation(candidate, "accepted", ranking.reasons)


def validate_source_candidates(
    track: Track, candidates: list[SourceCandidate] | tuple[SourceCandidate, ...]
) -> tuple[SourceValidation, ...]:
    """Validate candidates while preserving their input order."""
    return tuple(validate_source_candidate(track, candidate) for candidate in candidates)


__all__ = [
    "SourceValidation",
    "ValidationStatus",
    "validate_source_candidate",
    "validate_source_candidates",
]