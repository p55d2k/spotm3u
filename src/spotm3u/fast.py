"""Fast mode: the lightweight resolution path for users who want speed.

Fast mode keeps only what is required to turn a playlist track into an audio
file: local matching, the fewest search queries needed to find a downloadable
source, one download per track, and basic filesystem checks. Everything whose
only purpose is metadata quality or match confidence — source validation,
downloaded-audio validation, download retries across sources, metadata and
artwork enrichment, lyrics — is skipped.

It is a separate resolver rather than ``if fast_mode`` branches scattered
through the normal one, so normal mode keeps its conservative matching,
validation and enrichment exactly as before. Both paths share the same track
models, search primitives, downloader and M3U writer.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .audio.resolver import LocalAudioResolver
from .log import TrackLogger, track_identifier
from .models import ResolvedTrack, Track
from .online.downloader import DownloadError, download_track
from .online.ranking import CandidateRanking, rank_source_candidates
from .online.search import OnlineSourceSearcher, build_search_queries
from .resolution import (
    DownloadFunction,
    PreparedTrack,
    TrackResolution,
    TrackResolver,
    TrackStage,
    TrackStageCallback,
    _is_real_file,
)

logger = logging.getLogger(__name__)


class FastSourceSearcher:
    """Find the first usable source with as few search queries as possible.

    The normal search runs every configured query and aggregates the results so
    ranking can pick the best across all of them. Fast mode instead runs the
    queries one at a time, in order, and stops at the first query that yields an
    accepted candidate: once a suitable source is found there is no reason to
    keep searching for a potentially better one.
    """

    def __init__(self, searcher: OnlineSourceSearcher | None = None) -> None:
        self.searcher = searcher or OnlineSourceSearcher()

    def search(self, track: Track) -> tuple[CandidateRanking, ...]:
        """Return ranked candidates from the first query that has a usable hit.

        A query whose results are all rejected (wrong artist, karaoke, explicit
        live/remix mismatch, ...) is skipped and the next query is tried. An
        empty result means no query produced a usable source, which fast mode
        reports as a failed track instead of falling back to the normal
        pipeline.
        """
        for query in build_search_queries(track):
            candidates = self.searcher.search_query(track, query)
            if not candidates:
                continue
            usable = tuple(
                ranking for ranking in rank_source_candidates(track, candidates) if ranking.accepted
            )
            if usable:
                logger.info(
                    "fast search track=%s query=%r status=ok usable=%d stopped=true",
                    track_identifier(track),
                    query,
                    len(usable),
                )
                return usable
            logger.debug(
                "fast search track=%s query=%r status=no usable candidate",
                track_identifier(track),
                query,
            )
        logger.info("fast search track=%s status=no source found", track_identifier(track))
        return ()


class FastTrackResolver(TrackResolver):
    """Resolve tracks through the fast path: match, search, download, done.

    Inherits the shared resolution helpers (``resolve``, ``resolve_all``,
    ``resolve_report`` and the log context) and replaces only the two expensive
    phases. Nothing here validates a source or downloaded audio, enriches
    metadata, or consults the download cache, so a track costs at most one
    search series and one download.
    """

    def __init__(
        self,
        local_resolver: LocalAudioResolver,
        output_dir: str | Path,
        *,
        searcher: OnlineSourceSearcher | FastSourceSearcher | None = None,
        downloader: DownloadFunction = download_track,
        log: TrackLogger | None = None,
    ) -> None:
        super().__init__(
            local_resolver,
            output_dir,
            searcher=searcher or FastSourceSearcher(),
            downloader=downloader,
            cache=None,
            log=log,
        )

    def prepare(
        self,
        track: Track,
        *,
        stage_callback: TrackStageCallback | None = None,
    ) -> TrackResolution | PreparedTrack:
        """Match locally and find the first usable source, without downloading."""

        def report(stage: TrackStage) -> None:
            if stage_callback is not None:
                stage_callback(stage)

        log = self._log.with_track(track)
        log.info("stage=resolving-local mode=fast")
        report("resolving-local")
        local = self.local_resolver.resolve(track)
        if local.resolved is not None and _is_real_file(local.resolved.local_path):
            log.info("local match found path=%s mode=fast", local.resolved.local_path)
            resolved = ResolvedTrack(
                track,
                local.resolved.local_path,
                resolution_method="local",
                status="local",
            )
            return TrackResolution(track, "local", resolved=resolved, reasons=("local match",))

        log.info("stage=searching mode=fast")
        report("searching")
        try:
            rankings = self.searcher.search(track)
        except (OSError, RuntimeError, ValueError) as exc:
            log.error("fast source search failed: %s", exc)
            return TrackResolution(track, "failed", reasons=(f"source search failed: {exc}",))
        if not rankings:
            status = "ambiguous" if local.status == "ambiguous" else "missing"
            log.warning("fast search found no usable source status=%s", status)
            return TrackResolution(track, status, reasons=("no usable source found",))

        log.info("fast search resolved %d candidate(s) mode=fast", len(rankings))
        return PreparedTrack(track, tuple(ranking.candidate for ranking in rankings), rankings)

    def complete(
        self,
        prepared: PreparedTrack,
        *,
        stage_callback: TrackStageCallback | None = None,
    ) -> TrackResolution:
        """Download the first candidate that produces a complete file.

        Only candidates already returned by the single search are tried: no
        further searches and no source/audio validation. A download that fails
        moves on to the next candidate from the same result set, and if none
        succeeds the track fails without entering the normal pipeline.
        """

        def report(stage: TrackStage) -> None:
            if stage_callback is not None:
                stage_callback(stage)

        track = prepared.track
        log = self._log.with_track(track)
        download_failures = 0
        for ranking in prepared.rankings:
            report("downloading")
            log.info("stage=downloading url=%s mode=fast", ranking.candidate.url)
            try:
                downloaded = self.downloader(track, ranking.candidate.url, self.output_dir)
            except (DownloadError, OSError, RuntimeError) as exc:
                download_failures += 1
                log.warning("download failed url=%s error=%s", ranking.candidate.url, exc)
                continue
            if not _is_real_file(downloaded):
                download_failures += 1
                log.warning("download produced no file url=%s", ranking.candidate.url)
                continue
            log.info(
                "resolution status=downloaded mode=fast url=%s path=%s",
                ranking.candidate.url,
                downloaded,
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
                candidates=prepared.candidates,
                ranking=prepared.rankings,
            )

        if download_failures:
            log.error(
                "resolution status=failed mode=fast reasons=%d download attempt(s) failed",
                download_failures,
            )
            return TrackResolution(
                track,
                "failed",
                candidates=prepared.candidates,
                ranking=prepared.rankings,
                reasons=(f"{download_failures} download attempt(s) failed",),
            )
        log.error("resolution status=failed mode=fast reasons=no usable source")
        return TrackResolution(
            track,
            "failed",
            candidates=prepared.candidates,
            ranking=prepared.rankings,
            reasons=("no usable source",),
        )


__all__ = ["FastSourceSearcher", "FastTrackResolver"]
