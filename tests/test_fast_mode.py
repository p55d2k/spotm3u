"""Tests for fast mode: the lightweight audio-only resolution path."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from spotm3u.audio import LocalAudioResolver
from spotm3u.fast import FastSourceSearcher, FastTrackResolver
from spotm3u.jobs import ProcessingJob
from spotm3u.models import Track
from spotm3u.online import SourceCandidate, downloader
from spotm3u.online.downloader import download_track
from spotm3u.online.ranking import CandidateRanking, rank_source_candidates
from spotm3u.online.search import OnlineSourceSearcher, build_search_queries

TRACK = Track("Song", ["Artist"], duration_ms=200_000)
QUERIES = build_search_queries(TRACK)
SOURCE_URL = "https://example.com/source"


def candidate(**overrides) -> SourceCandidate:
    values = {
        "url": "https://example.com/song",
        "title": "Song - Official Audio",
        "artist": "Artist",
        "uploader": "Artist",
        "duration_s": 200,
        "source_type": "youtube",
    }
    values.update(overrides)
    return SourceCandidate(**values)


def accepted_rankings(*candidates: SourceCandidate) -> tuple[CandidateRanking, ...]:
    """Rank candidates the way the fast search does and keep the usable ones."""
    ranked = rank_source_candidates(TRACK, list(candidates))
    return tuple(ranking for ranking in ranked if ranking.accepted)


class StaticFastSearcher:
    """Stands in for the fast searcher in resolver tests."""

    def __init__(self, rankings: tuple[CandidateRanking, ...]) -> None:
        self.rankings = rankings

    def search(self, track: Track) -> tuple[CandidateRanking, ...]:
        return self.rankings


class RecordingSearcher:
    """Fake OnlineSourceSearcher that records the queries it is asked for."""

    def __init__(self, candidates_by_query: dict[str, list[SourceCandidate]]) -> None:
        self.candidates_by_query = candidates_by_query
        self.queries: list[str] = []

    def search_query(self, track: Track, query: str) -> tuple[SourceCandidate, ...]:
        self.queries.append(query)
        return tuple(self.candidates_by_query.get(query, ()))


def _forbidden(*_args, **_kwargs):
    raise AssertionError("fast mode must not validate or enrich")


def _block_normal_pipeline(monkeypatch) -> None:
    """Fail the test if any normal-mode stage is reached."""
    monkeypatch.setattr("spotm3u.resolution.validate_source_candidate", _forbidden)
    monkeypatch.setattr("spotm3u.resolution.validate_downloaded_audio", _forbidden)
    monkeypatch.setattr("spotm3u.resolution.enrich_metadata", _forbidden)
    monkeypatch.setattr("spotm3u.online.downloader.validate_downloaded_audio", _forbidden)
    monkeypatch.setattr("spotm3u.online.downloader.enrich_metadata", _forbidden)


# --- Fast source search ------------------------------------------------------


def test_fast_search_stops_at_the_first_query_with_a_usable_candidate() -> None:
    inner = RecordingSearcher({QUERIES[0]: [candidate()]})

    rankings = FastSourceSearcher(inner).search(TRACK)

    assert inner.queries == [QUERIES[0]], "no further query may run once one works"
    assert rankings
    assert all(ranking.accepted for ranking in rankings)


def test_fast_search_skips_a_query_whose_candidates_are_all_rejected() -> None:
    rejected = candidate(title="Song (Live)", url="https://example.com/live")
    inner = RecordingSearcher({QUERIES[0]: [rejected], QUERIES[1]: [candidate()]})

    rankings = FastSourceSearcher(inner).search(TRACK)

    assert inner.queries == [QUERIES[0], QUERIES[1]]
    assert [ranking.candidate.url for ranking in rankings] == [candidate().url]


def test_fast_search_returns_nothing_when_no_query_has_a_usable_candidate() -> None:
    inner = RecordingSearcher({})

    assert FastSourceSearcher(inner).search(TRACK) == ()
    assert inner.queries == list(QUERIES)


class CountingYoutubeDL:
    # Fake yt-dlp backend that records every search query it is asked for.

    calls: list[str] = []

    def __init__(self, options: dict) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, spec, download=False):
        query = spec.split(":", 1)[1]
        type(self).calls.append(query)
        # One plausible result per query, so normal mode has work to rank and
        # fast mode has something usable in the very first query.
        return {
            "entries": [
                {
                    "title": "Song - Official Audio",
                    "artist": "Artist",
                    "uploader": "Artist",
                    "webpage_url": "https://example.com/song",
                    "duration": 200,
                }
            ]
        }


def _count_search_queries(monkeypatch, run, tracks: int) -> int:
    """Run one resolution search path for ``tracks`` tracks and count queries."""
    CountingYoutubeDL.calls = []
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=CountingYoutubeDL))
    tracked = [TRACK] * tracks
    for track in tracked:
        run(track)
    return len(CountingYoutubeDL.calls)


def test_fast_mode_runs_far_fewer_search_queries_than_normal_mode(monkeypatch) -> None:
    """A 50-track playlist costs one query per track in fast mode."""

    def normal(track):
        return OnlineSourceSearcher(max_search_workers=1).search(track)

    normal_queries = _count_search_queries(monkeypatch, normal, 50)
    fast_queries = _count_search_queries(
        monkeypatch, lambda track: FastSourceSearcher().search(track), 50
    )

    assert normal_queries == 50 * len(QUERIES)
    assert fast_queries == 50, "fast mode stops at the first usable query per track"


# --- Fast resolution ---------------------------------------------------------


def test_fast_mode_downloads_a_track_without_validation_or_enrichment(
    tmp_path, monkeypatch
) -> None:
    _block_normal_pipeline(monkeypatch)
    downloaded = tmp_path / "output" / "song.mp3"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"mp3")
    downloads: list[tuple[str, Path]] = []

    def fake_downloader(track, url, output):
        downloads.append((url, Path(output)))
        return downloaded

    resolver = FastTrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        tmp_path / "output",
        searcher=StaticFastSearcher(accepted_rankings(candidate())),
        downloader=fake_downloader,
    )

    result = resolver.resolve(TRACK)

    assert result.status == "downloaded"
    assert result.successful
    assert result.local_path == downloaded
    assert downloads == [(candidate().url, tmp_path / "output")]


def test_fast_mode_reports_a_track_with_no_source_without_the_normal_pipeline(
    tmp_path, monkeypatch
) -> None:
    _block_normal_pipeline(monkeypatch)

    result = FastTrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        tmp_path / "output",
        searcher=StaticFastSearcher(()),
    ).resolve(TRACK)

    assert result.status == "missing"
    assert not result.successful
    assert result.reason == "no usable source found"


def test_fast_mode_uses_a_local_match_without_enriching_it(tmp_path, monkeypatch) -> None:
    _block_normal_pipeline(monkeypatch)
    music = tmp_path / "music"
    music.mkdir()
    local_file = music / "Artist - Song.mp3"
    local_file.write_bytes(b"audio")

    result = FastTrackResolver(
        LocalAudioResolver(music),
        tmp_path / "output",
        searcher=StaticFastSearcher(()),
    ).resolve(TRACK)

    assert result.status == "local"
    assert result.local_path == local_file


def test_fast_mode_tries_only_the_already_found_candidates_then_fails(tmp_path) -> None:
    rankings = accepted_rankings(
        candidate(url="https://example.com/first"),
        candidate(url="https://example.com/second", title="Song - Official Audio 2"),
    )
    assert len(rankings) == 2
    attempts: list[str] = []

    def failing_downloader(track, url, output):
        attempts.append(url)
        raise OSError("network down")

    result = FastTrackResolver(
        LocalAudioResolver(tmp_path / "empty"),
        tmp_path / "output",
        searcher=StaticFastSearcher(rankings),
        downloader=failing_downloader,
    ).resolve(TRACK)

    assert attempts == [ranking.candidate.url for ranking in rankings]
    assert result.status == "failed"
    assert "download attempt(s) failed" in result.reason


def test_fast_mode_playlist_keeps_order_and_survives_a_failed_track(tmp_path) -> None:
    tracks = [Track("First", ["Artist"]), Track("Second", ["Artist"]), Track("Third", ["Artist"])]

    class Searcher:
        def search(self, track: Track) -> tuple[CandidateRanking, ...]:
            if track.title == "Second":
                return ()
            return accepted_rankings(candidate(url=f"https://example.com/{track.title}"))

    def fake_downloader(track, url, output):
        path = Path(output) / f"{track.title}.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"mp3")
        return path

    job = ProcessingJob(
        job_id="fast",
        playlist_id="0",
        playlist_name="Playlist",
        tracks=tracks,
        output_dir=tmp_path / "output",
        resolver_factory=lambda: FastTrackResolver(
            LocalAudioResolver(tmp_path / "empty"),
            tmp_path / "output",
            searcher=Searcher(),
            downloader=fake_downloader,
        ),
        fast_mode=True,
    )
    job.start()
    job.wait(timeout=10)

    snapshot = job.as_dict()
    assert snapshot["status"] == "completed"
    assert snapshot["successful"] == 2
    assert snapshot["failed"] == 1
    assert snapshot["fast_mode"] is True
    m3u = (tmp_path / "output" / "playlist.m3u").read_text(encoding="utf-8")
    assert m3u.splitlines()[1:] == [
        "#EXTINF:0,Artist - First",
        (tmp_path / "output" / "First.mp3").as_posix(),
        "#EXTINF:0,Artist - Third",
        (tmp_path / "output" / "Third.mp3").as_posix(),
    ]


# --- Shared downloader: audio-only downloads ---------------------------------


class FakeYoutubeDL:
    options: dict = {}

    def __init__(self, options: dict) -> None:
        type(self).options = options

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def download(self, urls):
        Path(self.options["outtmpl"].replace("%(ext)s", "mp3")).write_bytes(b"mp3")
        return 0


def _install_fake_yt_dlp(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))


def test_audio_only_download_skips_validation_and_enrichment(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    def record(name, result):
        def call(*_args, **_kwargs):
            calls.append(name)
            return result

        return call

    _install_fake_yt_dlp(monkeypatch)
    monkeypatch.setattr(
        "spotm3u.online.downloader.validate_downloaded_audio",
        record("audio", types.SimpleNamespace(status="valid", reasons=())),
    )
    monkeypatch.setattr(
        "spotm3u.online.downloader.enrich_metadata",
        record("metadata", types.SimpleNamespace(errors=())),
    )

    result = download_track(TRACK, SOURCE_URL, tmp_path, verify=False)

    assert result.is_file()
    assert calls == []


def test_normal_download_still_validates_and_enriches(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    def record(name, result):
        def call(*_args, **_kwargs):
            calls.append(name)
            return result

        return call

    _install_fake_yt_dlp(monkeypatch)
    monkeypatch.setattr(
        "spotm3u.online.downloader.validate_downloaded_audio",
        record("audio", types.SimpleNamespace(status="valid", reasons=())),
    )
    monkeypatch.setattr(
        "spotm3u.online.downloader.enrich_metadata",
        record("metadata", types.SimpleNamespace(errors=())),
    )

    download_track(TRACK, SOURCE_URL, tmp_path)

    assert calls == ["audio", "metadata"]


def test_peer_download_is_reused_without_validation_when_verifying_is_off(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("spotm3u.online.downloader.validate_downloaded_audio", _forbidden)
    peer_path = tmp_path / "peer.mp3"
    peer_path.write_bytes(b"mp3")
    in_flight = downloader._InFlightDownload()
    in_flight.done = True
    in_flight.path = peer_path

    assert downloader._await_peer(in_flight, TRACK, 1, verify=False) == peer_path


def test_peer_download_is_validated_before_reuse_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("spotm3u.online.downloader.validate_downloaded_audio", _forbidden)
    peer_path = tmp_path / "peer.mp3"
    peer_path.write_bytes(b"mp3")
    in_flight = downloader._InFlightDownload()
    in_flight.done = True
    in_flight.path = peer_path

    with pytest.raises(AssertionError):
        downloader._await_peer(in_flight, TRACK, 1)
