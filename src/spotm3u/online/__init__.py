"""Online source search utilities for tracks without a local match."""

from .search import (
    OnlineSourceSearcher,
    SourceCandidate,
    build_search_queries,
    search_online_sources,
)
from .ranking import (
    CandidateRanking,
    ScoreComponents,
    rank_source_candidate,
    rank_source_candidates,
)
from .source_quality import (
    SourceProfile,
    SourceQuality,
    quality_label,
    quality_points,
    source_profile,
)
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
    "ScoreComponents",
    "rank_source_candidate",
    "rank_source_candidates",
    "SourceProfile",
    "SourceQuality",
    "quality_label",
    "quality_points",
    "source_profile",
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
