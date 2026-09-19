"""Online source search utilities for tracks without a local match."""

from ..metadata import MetadataResult, enrich_metadata
from .audio_validation import AudioValidation, AudioValidationStatus, validate_downloaded_audio
from .cache import DownloadCache, cache_metadata_key, source_identity
from .downloader import (
    DownloadError,
    describe_youtube_setup,
    download_source,
    download_track,
)
from .ranking import (
    CandidateRanking,
    ScoreComponents,
    rank_source_candidate,
    rank_source_candidates,
)
from .search import (
    OnlineSourceSearcher,
    SourceCandidate,
    build_search_queries,
    search_online_sources,
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
    "describe_youtube_setup",
    "download_source",
    "download_track",
    "DownloadCache",
    "cache_metadata_key",
    "source_identity",
    "AudioValidation",
    "AudioValidationStatus",
    "validate_downloaded_audio",
    "enrich_metadata",
    "MetadataResult",
]
