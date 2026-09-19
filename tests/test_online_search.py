"""Tests for the online source discovery layer."""

from __future__ import annotations

import sys
import threading
import time
import types

from spotm3u.models import Track
from spotm3u.online import SourceCandidate, build_search_queries
from spotm3u.online.search import OnlineSourceSearcher


def test_build_search_queries_include_title_artist_and_audio_hints() -> None:
    track = Track(title="Song Name", artists=["Artist One", "Artist Two"], album="Album Name")

    queries = build_search_queries(track)

    assert queries
    assert "artist one" in queries[0].lower()
    assert "artist two" in queries[0].lower()
    assert "song name" in queries[0].lower()
    assert any("official audio" in query.lower() for query in queries)
    assert any("lyrics" in query.lower() for query in queries)


def test_build_search_queries_keep_collaborating_artists_separate() -> None:
    queries = build_search_queries(Track(title="Song", artists=["Jay Chou", "Gary Yang"]))

    assert "jay chou gary yang song" in queries[0].lower()
    assert "jay chougary yang" not in " ".join(queries).lower()


def test_coerce_results_returns_empty_when_search_has_no_results() -> None:
    assert OnlineSourceSearcher._coerce_results({"entries": []}) == []


def test_source_candidate_keeps_metadata_for_ranking() -> None:
    candidate = SourceCandidate(
        url="https://example.com/watch?v=abc",
        title="Song Name",
        uploader="Artist",
        artist="Artist",
        duration_s=210.0,
        source_type="youtube",
        metadata={"extractor": "YouTube"},
    )

    assert candidate.url.startswith("https://")
    assert candidate.platform == "youtube"
    assert candidate.duration_s == 210.0


def test_search_filter_avoids_live_and_remix_indicators() -> None:
    track = Track(title="Song Name", artists=["Artist"])
    queries = build_search_queries(track)

    assert queries
    assert all("live" not in query.lower() for query in queries)
    assert all("remix" not in query.lower() for query in queries)


def test_instrumental_search_uses_instrumental_variant_without_lyrics() -> None:
    track = Track(title="Idea 22 Instrumental", artists=["Artist"])

    queries = build_search_queries(track)

    assert any("instrumental" in query.lower() for query in queries)
    assert not any("lyrics" in query.lower() for query in queries)


def test_build_search_queries_handle_punctuation_features_and_version() -> None:
    track = Track(
        title="Can't Stop (Live from Tokyo)",
        artists=["Artist One", "Guest Artist"],
        album="Album: Deluxe Edition",
    )

    queries = build_search_queries(track)

    assert queries[0] == "artist one guest artist can t stop live from tokyo"
    assert any("lyrics" in query for query in queries)


def test_coerce_results_skips_malformed_entries() -> None:
    info = {
        "entries": [
            {"title": "Song", "webpage_url": "https://example.com/song", "duration": "210"},
            {"title": "Missing URL"},
            "not metadata",
            {"webpage_url": "https://example.com/no-title"},
        ]
    }

    candidates = OnlineSourceSearcher._coerce_results(info)

    assert len(candidates) == 1
    assert candidates[0].duration_s == 210.0


def test_coerce_results_filters_non_music_entries() -> None:
    info = {
        "entries": [
            {
                "title": "Song - Official Audio",
                "webpage_url": "https://example.com/song",
            },
            {
                "title": "Song (Live)",
                "webpage_url": "https://example.com/live",
            },
            {
                "title": "Song Reaction",
                "webpage_url": "https://example.com/reaction",
            },
            {
                "title": "Song Movie Scene",
                "webpage_url": "https://example.com/scene",
            },
        ]
    }

    candidates = OnlineSourceSearcher._coerce_results(info)

    assert [candidate.url for candidate in candidates] == [
        "https://example.com/song",
        "https://example.com/live",
    ]


class FakeYoutubeDL:
    """Shared-state fake so the test can observe real concurrency."""

    instance = None

    def __init__(self, options: dict) -> None:
        type(self).instance = self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class MeasuringYoutubeDL(FakeYoutubeDL):
    lock = threading.Lock()
    active = 0
    max_active = 0
    shared_url = "https://example.com/shared"

    def extract_info(self, spec, download=False):
        query = spec.split(":", 1)[1]
        with type(self).lock:
            type(self).active += 1
            type(self).max_active = max(type(self).max_active, type(self).active)
        try:
            time.sleep(0.05)
            if "audio" in query:
                url = type(self).shared_url
            else:
                url = "https://example.com/" + "_".join(query.split())
            return {"entries": [{"title": query, "webpage_url": url}]}
        finally:
            with type(self).lock:
                type(self).active -= 1


def install_fake_yt_dlp(monkeypatch, fake=MeasuringYoutubeDL) -> None:
    fake.active = 0
    fake.max_active = 0
    monkeypatch.setitem(
        sys.modules,
        "yt_dlp",
        types.SimpleNamespace(
            YoutubeDL=fake,
            utils=types.SimpleNamespace(DownloadError=RuntimeError),
        ),
    )


def test_search_runs_queries_concurrently_and_deduplicates(monkeypatch) -> None:
    install_fake_yt_dlp(monkeypatch)
    track = Track(title="Song", artists=["Artist"])
    queries = build_search_queries(track)
    assert len(queries) >= 3

    results = OnlineSourceSearcher().search(track)

    assert MeasuringYoutubeDL.max_active >= 2, "queries did not run in parallel"
    urls = {candidate.url for candidate in results}
    assert MeasuringYoutubeDL.shared_url in urls
    assert len(urls) == len(queries) - 1  # the two 'audio' queries share one URL


def test_search_returns_empty_without_yt_dlp(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "yt_dlp", None)
    results = OnlineSourceSearcher().search(Track(title="Song", artists=["Artist"]))
    assert results == ()


def test_search_applies_socket_timeout_to_query_options(monkeypatch) -> None:
    captured: dict = {}

    class NoResultYtdl:
        def __init__(self, options: dict):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, spec, download=False):
            return {"entries": []}

    monkeypatch.setitem(
        sys.modules,
        "yt_dlp",
        types.SimpleNamespace(
            YoutubeDL=NoResultYtdl,
            utils=types.SimpleNamespace(DownloadError=RuntimeError),
        ),
    )

    OnlineSourceSearcher(max_search_workers=1, socket_timeout=42).search(
        Track(title="Song", artists=["Artist"])
    )

    assert captured.get("socket_timeout") == 42
