"""End-to-end resolution of playlist tracks to verified local audio files."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Callable, Literal

from .audio.resolver import LocalAudioResolver
from .log import TrackLogger
from .models import ResolvedTrack, Track
from .online.audio_validation import AudioValidation, validate_downloaded_audio
from .online.cache import DownloadCache
from .online.downloader import DownloadError, download_track
from .online.ranking import CandidateRanking, rank_source_candidates
from .online.search import OnlineSourceSearcher, SourceCandidate
from .online.validation import SourceValidation, validate_source_candidate

logger = logging.getLogger(__name__)

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
        log: TrackLogger | None = None,
    ) -> None:
        self.local_resolver = local_resolver
        self.output_dir = Path(output_dir)
        self.searcher = searcher or OnlineSourceSearcher()
        self.downloader = downloader
        self.cache = cache
        self._log = log or TrackLogger(logger)

    def set_log_context(self, log: TrackLogger) -> None:
        """Replace the logging context (e.g. to add a background job id)."""
        if log is not None:
            self._log = log

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

        log = self._log.with_track(track)
        log.info("stage=resolving-local")
        report("resolving-local")
        local = self.local_resolver.resolve(track)
        if local.resolved is not None and _is_real_file(local.resolved.local_path):
            resolved = ResolvedTrack(
                track,
                local.resolved.local_path,
                resolution_method="local",
                status="local",
            )
            log.info("local match found path=%s", local.resolved.local_path)
            return TrackResolution(
                track, "local", resolved=resolved, candidates=(), reasons=("local match",)
            )

        log.info("no local match status=%s", local.status)
        log.info("stage=searching")
        report("searching")
        try:
            candidates = tuple(self.searcher.search(track))
        except (OSError, RuntimeError, ValueError) as exc:
            log.error("source search failed: %s", exc)
            return TrackResolution(track, "failed", reasons=(f"source search failed: {exc}",))
        if not candidates:
            status: ResolutionStatus = "ambiguous" if local.status == "ambiguous" else "missing"
            log.warning("search returned no candidates status=%s", status)
            return TrackResolution(track, status, reasons=("no online source candidates",))

        rankings = rank_source_candidates(track, candidates)
        log.info("search returned %d candidates status=ok", len(candidates))
        log.debug("ranking computed for %d candidates", len(rankings))
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

        log = self._log.with_track(track)
        rejected_urls: list[str] = []
        download_failures = 0
        invalid_downloads: list[str] = []
        uncertain_download: tuple[
            Path, CandidateRanking, SourceValidation, AudioValidation
        ] | None = None
        for position, ranking in enumerate(rankings, start=1):
            report("validating-source")
            source_validation = validate_source_candidate(track, ranking.candidate)
            log.info(
                "stage=validating-source candidate=%d url=%s [%s] score=%.2f source_status=%s",
                position,
                ranking.candidate.url,
                ranking.candidate.source_query or "?",
                ranking.score,
                source_validation.status,
            )
            if source_validation.status == "rejected":
                rejected_urls.append(ranking.candidate.url)
                log.warning(
                    "candidate rejected url=%s reasons=%s",
                    ranking.candidate.url,
                    "; ".join(source_validation.reasons) or "no reason given",
                )
                continue

            report("downloading")
            log.info("stage=downloading url=%s", ranking.candidate.url)
            downloaded: Path | None = None
            reused_from_cache = False
            if self.cache is not None:
                cached = self.cache.lookup(track, ranking.candidate.url)
                if cached is not None:
                    downloaded = cached
                    reused_from_cache = True
                    log.info("download reused from cache path=%s", cached)
            if downloaded is None:
                try:
                    downloaded = self.downloader(
                        track, ranking.candidate.url, self.output_dir
                    )
                except (DownloadError, OSError, RuntimeError) as exc:
                    download_failures += 1
                    log.warning("download failed url=%s error=%s", ranking.candidate.url, exc)
                    continue
                log.info("download completed path=%s", downloaded)

            report("validating-audio")
            audio_validation = validate_downloaded_audio(track, downloaded)
            log.info(
                "stage=validating-audio path=%s audio_status=%s reasons=%s",
                downloaded,
                audio_validation.status,
                "; ".join(audio_validation.reasons) or "none",
            )
            if audio_validation.status == "invalid" or not _is_real_file(downloaded):
                failure_reason = "downloaded file is not usable"
                if audio_validation.reasons:
                    failure_reason = "; ".join(audio_validation.reasons)
                invalid_downloads.append(failure_reason)
                if not reused_from_cache and downloaded is not None:
                    _discard_file(downloaded)
                log.warning(
                    "downloaded audio invalid path=%s reason=%s",
                    downloaded,
                    failure_reason,
                )
                continue
            if audio_validation.status == "uncertain":
                uncertain_download = (
                    downloaded,
                    ranking,
                    source_validation,
                    audio_validation,
                )
                log.warning(
                    "downloaded audio unverified path=%s reasons=%s",
                    downloaded,
                    "; ".join(audio_validation.reasons) or "no reason given",
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
            log.info("resolution status=downloaded url=%s path=%s", ranking.candidate.url, downloaded)
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
            log.warning(
                "resolution status=uncertain url=%s path=%s reasons=%s",
                ranking.candidate.url,
                downloaded,
                "; ".join(audio_validation.reasons) or "downloaded audio is unverified",
            )
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
            log.error(
                "resolution status=failed reasons=all downloaded candidates failed audio validation; %s",
                "; ".join(invalid_downloads),
            )
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
            log.error(
                "resolution status=failed reasons=%d download attempt(s) failed",
                download_failures,
            )
            return TrackResolution(
                track,
                "failed",
                candidates=candidates,
                ranking=rankings,
                reasons=(f"{download_failures} download attempt(s) failed",),
            )

        log.error(
            "resolution status=rejected reasons=no candidate passed source validation"
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
