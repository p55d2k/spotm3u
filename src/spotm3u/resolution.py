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

TrackStage = Literal[
    "queued",
    "resolving-local",
    "searching",
    "validating-source",
    "downloading",
    "validating-audio",
    "complete",
    "failed",
    "ambiguous",
    "skipped",
]
TrackStageCallback = Callable[[TrackStage], None]


@dataclass(frozen=True)
class TrackResolution:
    """The terminal result for one playlist track."""

    track: Track
    status: ResolutionStatus
    resolved: ResolvedTrack | None = None
    output_path: Path | None = None
    source_url: str | None = None
    candidates: tuple[SourceCandidate, ...] = ()
    ranking: tuple[CandidateRanking, ...] = ()
    validation: SourceValidation | None = None
    audio_validation: AudioValidation | None = None
    reasons: tuple[str, ...] = ()

    @property
    def local_path(self) -> Path | None:
        return self.resolved.local_path if self.resolved else self.output_path

    @property
    def successful(self) -> bool:
        return self.status in {"local", "downloaded"} and self.resolved is not None

    @property
    def reason(self) -> str:
        """Return a concise explanation suitable for a job result."""
        return "; ".join(self.reasons)

    def as_dict(self) -> dict[str, object]:
        """Return the result in a JSON-compatible job-result shape."""
        track = self.track
        return {
            "track": {
                "title": track.title,
                "artists": list(track.artists),
                "album": track.album,
                "duration_ms": track.duration_ms,
                "spotify_id": track.spotify_id,
                "spotify_url": track.spotify_url,
            },
            "status": self.status,
            "reason": self.reason,
            "source_url": self.source_url,
            "local_path": str(self.local_path) if self.local_path is not None else None,
        }


@dataclass(frozen=True)
class ResolutionReport:
    """The complete, ordered result of processing a playlist job."""

    results: tuple[TrackResolution, ...]

    @property
    def successful(self) -> tuple[TrackResolution, ...]:
        return tuple(result for result in self.results if result.successful)

    @property
    def unsuccessful(self) -> tuple[TrackResolution, ...]:
        return tuple(result for result in self.results if not result.successful)

    @property
    def counts(self) -> dict[str, int]:
        return {
            status: sum(result.status == status for result in self.results)
            for status in (
                "local",
                "downloaded",
                "missing",
                "ambiguous",
                "rejected",
                "failed",
                "uncertain",
            )
        }

    def as_dict(self) -> dict[str, object]:
        """Return all per-track outcomes and status counts for the job."""
        return {
            "total": len(self.results),
            "successful": len(self.successful),
            "counts": self.counts,
            "tracks": [result.as_dict() for result in self.results],
        }


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

    def resolve(
        self,
        track: Track,
        *,
        stage_callback: TrackStageCallback | None = None,
    ) -> TrackResolution:
        """Resolve one track without allowing an uncertain result to succeed."""
        def report(stage: TrackStage) -> None:
            if stage_callback is not None:
                stage_callback(stage)

        report("resolving-local")
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

        report("searching")
        try:
            candidates = tuple(self.searcher.search(track))
        except (OSError, RuntimeError, ValueError) as exc:
            return TrackResolution(track, "failed", reasons=(f"source search failed: {exc}",))
        if not candidates:
            status: ResolutionStatus = "ambiguous" if local.status == "ambiguous" else "missing"
            return TrackResolution(track, status, reasons=("no online source candidates",))

        rankings = rank_source_candidates(track, candidates)
        ambiguous_seen = False
        rejected_seen = False
        rejected_url: str | None = None
        for ranking in rankings:
            report("validating-source")
            source_validation = validate_source_candidate(track, ranking.candidate)
            if source_validation.status == "uncertain":
                ambiguous_seen = True
                continue
            if source_validation.status == "rejected":
                rejected_seen = True
                rejected_url = rejected_url or ranking.candidate.url
                continue

            report("downloading")
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

            report("validating-audio")
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
                    output_path=downloaded,
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
                    output_path=downloaded,
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

        status: ResolutionStatus
        if rejected_seen:
            status = "rejected"
        elif ambiguous_seen:
            status = "ambiguous"
        else:
            status = "missing"
        return TrackResolution(
            track,
            status,
            source_url=rejected_url,
            candidates=candidates,
            ranking=rankings,
            reasons=("no candidate passed source validation",),
        )

    def resolve_all(self, tracks: list[Track]) -> list[TrackResolution]:
        """Resolve tracks in playlist order, including intentional duplicates."""
        return [self.resolve(track) for track in tracks]

    def resolve_report(self, tracks: list[Track]) -> ResolutionReport:
        """Resolve a playlist and retain every outcome as a job report."""
        return ResolutionReport(tuple(self.resolve_all(tracks)))


def _is_real_file(path: str | Path) -> bool:
    try:
        return Path(path).is_file()
    except (OSError, ValueError):
        return False


__all__ = [
    "ResolutionReport",
    "ResolutionStatus",
    "TrackResolution",
    "TrackResolver",
    "TrackStage",
    "TrackStageCallback",
]
