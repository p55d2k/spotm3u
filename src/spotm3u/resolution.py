"""End-to-end resolution of playlist tracks to verified local audio files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from .audio.resolver import LocalAudioResolver
from .models import ResolvedTrack, Track
from .online.audio_validation import AudioValidation, validate_downloaded_audio
from .online.downloader import DownloadError, download_track
from .online.ranking import CandidateRanking, rank_source_candidates
from .online.search import OnlineSourceSearcher, SourceCandidate
from .online.validation import SourceValidation, validate_source_candidate

ResolutionStatus = Literal[
    "local", "downloaded", "missing", "ambiguous", "rejected", "failed", "uncertain"
]
DownloadFunction = Callable[[Track, str, str | Path], Path]


@dataclass(frozen=True)
class TrackResolution:
    """The terminal result for one playlist track."""

    track: Track
    status: ResolutionStatus
    resolved: ResolvedTrack | None = None
    source_url: str | None = None
    candidates: tuple[SourceCandidate, ...] = ()
    ranking: tuple[CandidateRanking, ...] = ()
    validation: SourceValidation | None = None
    audio_validation: AudioValidation | None = None
    reasons: tuple[str, ...] = ()

    @property
    def local_path(self) -> Path | None:
        return self.resolved.local_path if self.resolved else None

    @property
    def successful(self) -> bool:
        return self.status in {"local", "downloaded"} and self.resolved is not None


class TrackResolver:
    """Resolve tracks locally, then through the complete online pipeline."""

    def __init__(
        self,
        local_resolver: LocalAudioResolver,
        output_dir: str | Path,
        *,
        searcher: OnlineSourceSearcher | None = None,
        downloader: DownloadFunction = download_track,
    ) -> None:
        self.local_resolver = local_resolver
        self.output_dir = Path(output_dir)
        self.searcher = searcher or OnlineSourceSearcher()
        self.downloader = downloader

    def resolve(self, track: Track) -> TrackResolution:
        """Resolve one track without allowing an uncertain result to succeed."""
        local = self.local_resolver.resolve(track)
        if local.resolved is not None and _is_real_file(local.resolved.local_path):
            resolved = ResolvedTrack(
                track,
                local.resolved.local_path,
                resolution_method="local",
                status="local",
            )
            return TrackResolution(
                track, "local", resolved=resolved, candidates=(), reasons=("local match",)
            )

        try:
            candidates = tuple(self.searcher.search(track))
        except (OSError, RuntimeError, ValueError) as exc:
            return TrackResolution(track, "failed", reasons=(f"source search failed: {exc}",))
        if not candidates:
            status: ResolutionStatus = "ambiguous" if local.status == "ambiguous" else "missing"
            return TrackResolution(track, status, reasons=("no online source candidates",))

        rankings = rank_source_candidates(track, candidates)
        uncertain_seen = False
        rejected_seen = False
        for ranking in rankings:
            source_validation = validate_source_candidate(track, ranking.candidate)
            if source_validation.status == "uncertain":
                uncertain_seen = True
                continue
            if source_validation.status == "rejected":
                rejected_seen = True
                continue

            try:
                downloaded = self.downloader(
                    track, ranking.candidate.url, self.output_dir
                )
            except (DownloadError, OSError, RuntimeError) as exc:
                return TrackResolution(
                    track,
                    "failed",
                    source_url=ranking.candidate.url,
                    candidates=candidates,
                    ranking=rankings,
                    validation=source_validation,
                    reasons=(f"download failed: {exc}",),
                )

            audio_validation = validate_downloaded_audio(track, downloaded)
            if audio_validation.status == "invalid":
                return TrackResolution(
                    track,
                    "rejected",
                    source_url=ranking.candidate.url,
                    candidates=candidates,
                    ranking=rankings,
                    validation=source_validation,
                    audio_validation=audio_validation,
                    reasons=audio_validation.reasons,
                )
            if audio_validation.status == "uncertain" or not _is_real_file(downloaded):
                return TrackResolution(
                    track,
                    "uncertain",
                    source_url=ranking.candidate.url,
                    candidates=candidates,
                    ranking=rankings,
                    validation=source_validation,
                    audio_validation=audio_validation,
                    reasons=audio_validation.reasons or ("downloaded file is not usable",),
                )

            resolved = ResolvedTrack(
                track,
                downloaded,
                resolution_method="downloaded",
                source_url=ranking.candidate.url,
                status="downloaded",
            )
            return TrackResolution(
                track,
                "downloaded",
                resolved=resolved,
                source_url=ranking.candidate.url,
                candidates=candidates,
                ranking=rankings,
                validation=source_validation,
                audio_validation=audio_validation,
            )

        status = "uncertain" if uncertain_seen else "rejected" if rejected_seen else "missing"
        return TrackResolution(
            track,
            status,
            candidates=candidates,
            ranking=rankings,
            reasons=("no candidate passed source validation",),
        )

    def resolve_all(self, tracks: list[Track]) -> list[TrackResolution]:
        """Resolve tracks in playlist order, including intentional duplicates."""
        return [self.resolve(track) for track in tracks]


def _is_real_file(path: str | Path) -> bool:
    try:
        return Path(path).is_file()
    except (OSError, ValueError):
        return False


__all__ = ["ResolutionStatus", "TrackResolution", "TrackResolver"]
