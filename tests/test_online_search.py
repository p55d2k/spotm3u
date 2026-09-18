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
