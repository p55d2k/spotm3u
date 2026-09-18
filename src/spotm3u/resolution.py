"""End-to-end resolution of playlist tracks to verified local audio files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from .audio.resolver import LocalAudioResolver
from .models import ResolvedTrack, Track
from .online.audio_validation import AudioValidation, validate_downloaded_audio
from .online.cache import DownloadCache
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


@dataclass(frozen=True)
class PreparedTrack:
    """A track whose local resolution, search, and ranking are complete.

    ``PreparedTrack`` means the track still needs its download phase. Terminal
    ``TrackResolution`` outcomes are returned instead of a ``PreparedTrack``
    when no download is needed or possible.
    """

    track: Track
    candidates: tuple[SourceCandidate, ...]
    rankings: tuple[CandidateRanking, ...]


class TrackResolver:
    """Resolve tracks locally, then through the complete online pipeline."""

    def __init__(
        self,
        local_resolver: LocalAudioResolver,
        output_dir: str | Path,
        *,
        searcher: OnlineSourceSearcher | None = None,
        downloader: DownloadFunction = download_track,
        cache: DownloadCache | None = None,
    ) -> None:
        self.local_resolver = local_resolver
        self.output_dir = Path(output_dir)
        self.searcher = searcher or OnlineSourceSearcher()
        self.downloader = downloader
        self.cache = cache

    def prepare(
        self,
        track: Track,
        *,
        stage_callback: TrackStageCallback | None = None,
    ) -> TrackResolution | PreparedTrack:
        """Run the local match, search, and ranking phases for ``track``.

        This never downloads. Terminal ``TrackResolution`` results (local
        match, missing, ambiguous, failed search) are returned as-is; tracks
        with download candidates come back as a :class:`PreparedTrack` for a
        later :meth:`complete` call. Splitting the phases lets callers search
        every track up front while later downloads are still running.
        """
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
        return PreparedTrack(track, candidates, rankings)

    def complete(
        self,
        prepared: PreparedTrack,
        *,
        stage_callback: TrackStageCallback | None = None,
    ) -> TrackResolution:
        """Download and validate the best candidate for a prepared track.

        ``prepared`` is a :class:`PreparedTrack` returned by :meth:`prepare`.
        Candidates are tried in ranking order, downloading and validating each
        until one passes audio validation or every candidate is exhausted.
        """
        track = prepared.track
        candidates = prepared.candidates
        rankings = prepared.rankings

        def report(stage: TrackStage) -> None:
            if stage_callback is not None:
                stage_callback(stage)

        rejected_urls: list[str] = []
        download_failures = 0
        invalid_downloads: list[str] = []
        uncertain_download: tuple[
            Path, CandidateRanking, SourceValidation, AudioValidation
        ] | None = None
        for ranking in rankings:
            report("validating-source")
            source_validation = validate_source_candidate(track, ranking.candidate)
            if source_validation.status == "rejected":
                rejected_urls.append(ranking.candidate.url)
                continue

            report("downloading")
            downloaded: Path | None = None
            reused_from_cache = False
            if self.cache is not None:
                cached = self.cache.lookup(track, ranking.candidate.url)
                if cached is not None:
                    downloaded = cached
                    reused_from_cache = True
            if downloaded is None:
                try:
                    downloaded = self.downloader(
                        track, ranking.candidate.url, self.output_dir
                    )
                except (DownloadError, OSError, RuntimeError) as exc:
                    download_failures += 1
                    continue

            report("validating-audio")
            audio_validation = validate_downloaded_audio(track, downloaded)
            if audio_validation.status == "invalid" or not _is_real_file(downloaded):
                if not reused_from_cache and downloaded is not None:
                    _discard_file(downloaded)
                invalid_downloads.extend(
                    audio_validation.reasons or ("downloaded file is not usable",)
                )
                continue
            if audio_validation.status == "uncertain":
                uncertain_download = (
                    downloaded,
                    ranking,
                    source_validation,
                    audio_validation,
                )
                continue

            if self.cache is not None and not reused_from_cache:
                self.cache.store(track, ranking.candidate.url, downloaded)

            resolved = ResolvedTrack(
                track,
                downloaded,
                resolution_method="downloaded",
                source_url=ranking.candidate.url,
                status="downloaded",
            )
            reasons = ("reused cached download",) if reused_from_cache else ()
            return TrackResolution(
                track,
                "downloaded",
                resolved=resolved,
                source_url=ranking.candidate.url,
                candidates=candidates,
                ranking=rankings,
                validation=source_validation,
                audio_validation=audio_validation,
                reasons=reasons,
            )

        if uncertain_download is not None:
            downloaded, ranking, source_validation, audio_validation = uncertain_download
            return TrackResolution(
                track,
                "uncertain",
                source_url=ranking.candidate.url,
                candidates=candidates,
                ranking=rankings,
                validation=source_validation,
                audio_validation=audio_validation,
                output_path=downloaded,
                reasons=audio_validation.reasons or ("downloaded audio is unverified",),
            )

        if invalid_downloads:
            return TrackResolution(
                track,
                "failed",
                candidates=candidates,
                ranking=rankings,
                reasons=(
                    "all downloaded candidates failed audio validation",
                    *invalid_downloads,
                ),
            )

        if download_failures:
            return TrackResolution(
                track,
                "failed",
                candidates=candidates,
                ranking=rankings,
                reasons=(f"{download_failures} download attempt(s) failed",),
            )

        return TrackResolution(
            track,
            "rejected",
            source_url=rejected_urls[0] if rejected_urls else None,
            candidates=candidates,
            ranking=rankings,
            reasons=("no candidate passed source validation",),
        )

    def resolve(
        self,
        track: Track,
        *,
        stage_callback: TrackStageCallback | None = None,
    ) -> TrackResolution:
        """Resolve one track without allowing an uncertain result to succeed."""
        prepared = self.prepare(track, stage_callback=stage_callback)
        if not isinstance(prepared, PreparedTrack):
            return prepared
        return self.complete(prepared, stage_callback=stage_callback)

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


def _discard_file(path: str | Path) -> None:
    """Best-effort removal of a download that failed validation."""
    try:
        Path(path).unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


__all__ = [
    "PreparedTrack",
    "ResolutionReport",
    "ResolutionStatus",
    "TrackResolution",
    "TrackResolver",
    "TrackStage",
    "TrackStageCallback",
]
