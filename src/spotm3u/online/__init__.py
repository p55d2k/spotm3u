"""Online source search utilities for tracks without a local match."""

from .search import (
    OnlineSourceSearcher,
    SourceCandidate,
    build_search_queries,
    search_online_sources,
)
from .ranking import CandidateRanking, rank_source_candidate, rank_source_candidates
from .validation import (
    SourceValidation,
    ValidationStatus,
    validate_source_candidate,
    validate_source_candidates,
)
from .downloader import DownloadError, download_source, download_track
from .audio_validation import AudioValidation, AudioValidationStatus, validate_downloaded_audio

__all__ = [
    "OnlineSourceSearcher",
    "SourceCandidate",
    "build_search_queries",
    "search_online_sources",
    "CandidateRanking",
    "rank_source_candidate",
    "rank_source_candidates",
    "SourceValidation",
    "ValidationStatus",
    "validate_source_candidate",
    "validate_source_candidates",
    "DownloadError",
    "download_source",
    "download_track",
    "AudioValidation",
    "AudioValidationStatus",
    "validate_downloaded_audio",
]
