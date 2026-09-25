"""Deterministic regression suite for track matching and source selection.

This is the single place that pins the *decisions* of the matching pipeline so
that changing normalization, query generation, candidate scoring/filtering,
source validation or final selection cannot silently fix one class of songs
while breaking another.

Coverage runs the whole path from Spotify track metadata to the final
accepted/rejected candidate. Tests assert observable decisions
(``accepted``/``confidence``/reasons/ordering), never private helper call
counts, so refactoring does not make them brittle. Fixtures are representative
metadata rather than live search results, so the suite needs no network access.

Live, network-dependent matching checks live in
``test_matching_regression_live.py`` and are opt-in only.
"""

from __future__ import annotations

import pytest

from spotm3u.models import Track
from spotm3u.normalization import normalize, normalize_artists, normalize_cjk
from spotm3u.online import (
    SourceCandidate,
    SourceQuality,
    build_search_queries,
    rank_source_candidate,
    rank_source_candidates,
    source_profile,
    validate_source_candidate,
)
from spotm3u.online.search import OnlineSourceSearcher

# ---------------------------------------------------------------------------
# Shared fixtures and factories
# ---------------------------------------------------------------------------


def make_track(
    title: str,
    artists: list[str] | tuple[str, ...] = ("Artist",),
    *,
    duration_ms: int | None = 210_000,
    **kwargs,
) -> Track:
    """Build the Spotify-side track under test."""
    return Track(title=title, artists=list(artists), duration_ms=duration_ms, **kwargs)


def make_candidate(
    title: str,
    *,
    artist: str | None = "Artist",
    uploader: str | None = "Artist",
    duration_s: float | None = 210.0,
    url: str = "https://example.com/candidate",
    source_query: str | None = None,
    **metadata,
) -> SourceCandidate:
    """Build a YouTube-side candidate with representative metadata."""
    if url == "https://example.com/candidate":
        url = "https://example.com/" + "_".join(title.casefold().split()) or url
    return SourceCandidate(
        url=url,
        title=title,
        artist=artist,
        uploader=uploader,
        duration_s=duration_s,
        source_type="youtube",
        metadata=metadata,
        source_query=source_query,
    )


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_normalization_ignores_punctuation_and_case() -> None:
    assert normalize("Song Name!") == normalize("song name")
    assert normalize("Can't Stop") == normalize("Can t Stop")
    assert normalize("  Beyoncé — Déjà Vu!  ") == "beyonce deja vu"


def test_normalization_keeps_unscripted_letters_and_numbers() -> None:
    assert normalize("Артист_日本語") == "артист 日本語"
    assert normalize("Track 01") == "track 01"


def test_normalization_strips_emoji_without_losing_the_title() -> None:
    # Regression: Coldplay uploads/Spotify titles include ❤️ / ♾️. Symbols are
    # separators, never part of the comparison string.
    assert normalize("Higher Power ♾️") == "higher power"
    assert normalize("My Universe ❤️") == "my universe"


def test_cjk_normalization_unifies_traditional_and_simplified_scripts() -> None:
    # Regression: YouTube uses Traditional Chinese for songs Spotify lists in
    # Simplified form (薛之謙 vs 薛之谦, 海闊天空 vs 海阔天空).
    assert normalize_cjk("海闊天空") == normalize_cjk("海阔天空")
    assert normalize_cjk("薛之謙") == normalize_cjk("薛之谦")


def test_featured_artist_spellings_normalize_to_one_identity() -> None:
    assert normalize_artists("Artist feat. Guest") == "artist guest"
    assert normalize_artists("Artist ft. Guest") == "artist guest"
    assert normalize_artists(["Artist", "Guest"]) == "artist guest"


# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------


def test_queries_are_artist_aware_and_audio_focused() -> None:
    queries = build_search_queries(make_track("Song Name", ["Artist One", "Artist Two"]))

    assert queries
    assert "artist one artist two song name" in queries[0].casefold()
    assert any("official audio" in query.casefold() for query in queries)
    # Lyrics come from the dedicated lyrics library, not from search.
    assert not any("lyric" in query.casefold() for query in queries)


def test_queries_avoid_live_and_remix_variants() -> None:
    queries = build_search_queries(make_track("Song Name"))

    assert all("live" not in query.casefold() for query in queries)
    assert all("remix" not in query.casefold() for query in queries)


def test_instrumental_request_searches_the_instrumental_variant() -> None:
    queries = build_search_queries(make_track("Idea 22 Instrumental"))

    assert any("instrumental" in query.casefold() for query in queries)
    assert not any("lyric" in query.casefold() for query in queries)


@pytest.mark.parametrize(
    ("title", "first_query", "expected"),
    [
        pytest.param(
            "❤️",
            "coldplay ❤",
            ("coldplay heart", "coldplay red heart"),
            id="heart",
        ),
        pytest.param("♾️", "coldplay ♾", ("coldplay infinity",), id="infinity"),
    ],
)
def test_regression_coldplay_symbol_titles_gain_searchable_queries(
    title: str, first_query: str, expected: tuple[str, ...]
) -> None:
    """Coldplay tracks titled with only a symbol (``❤️`` / ``♾️``).

    Regression: the symbol was treated as punctuation, the title normalized to
    nothing, and the track produced no search queries at all. The title as
    written stays searchable and the symbol is expanded into its meaning.
    """
    queries = build_search_queries(make_track(title, ["Coldplay"]))

    assert queries
    assert queries[0] == first_query
    for query in expected:
        assert query in queries


def test_regression_symbol_titles_keep_the_plain_queries_first() -> None:
    """An emoji beside real title text must not disturb the existing queries."""
    plain = build_search_queries(make_track("Higher Power", ["Coldplay"]))
    decorated = build_search_queries(make_track("Higher Power ♾️", ["Coldplay"]))

    assert decorated[: len(plain)] == plain
    assert "coldplay higher power infinity" in decorated[len(plain) :]


def test_title_only_queries_when_artist_is_unknown() -> None:
    queries = build_search_queries(make_track("Song Name", artists=[]))

    assert queries
    assert queries[0] == "song name"
    assert any("official audio" in query.casefold() for query in queries)


# ---------------------------------------------------------------------------
# Candidate scoring / recording identity
# ---------------------------------------------------------------------------


def test_exact_title_and_artist_is_a_strong_match() -> None:
    result = rank_source_candidate(
        make_track("Song Name"), make_candidate("Song Name - Official Audio")
    )

    assert result.accepted
    assert result.confidence == "strong"
    assert any("artist matches" in reason for reason in result.reasons)


@pytest.mark.parametrize(
    "candidate_title",
    [
        pytest.param("Song Name.", id="trailing-punctuation"),
        pytest.param("Song Name - Remastered 2011", id="remastered"),
        pytest.param("Song Name [Official]", id="bracketed-official"),
        pytest.param("Artist - Song Name", id="artist-prefixed-title"),
    ],
)
def test_minor_title_differences_are_accepted(candidate_title: str) -> None:
    assert rank_source_candidate(make_track("Song Name"), make_candidate(candidate_title)).accepted


def test_artist_attribution_is_identity_not_a_title_difference() -> None:
    # Regression: YouTube titles frequently embed the artist (薛之谦 演员). The
    # artist name must be treated as identity evidence, not as title text.
    result = rank_source_candidate(
        make_track("演员", ["薛之谦"]),
        make_candidate("薛之谦 - 演员 Official Audio", artist="薛之谦", uploader="薛之谦"),
    )

    assert result.accepted
    assert result.components is not None
    assert result.components.title >= 30.0  # title similarity is effectively a full match


def test_artist_name_variations_without_diacritics_match() -> None:
    result = rank_source_candidate(
        make_track("Déjà Vu", ["Beyoncé"]),
        make_candidate("Déjà Vu", artist="Beyonce", uploader="Beyonce"),
    )

    assert result.accepted
    assert any("artist matches" in reason for reason in result.reasons)


def test_topic_channel_upload_confirms_the_artist() -> None:
    result = rank_source_candidate(
        make_track("Wonderwall", ["Oasis"]),
        make_candidate("Wonderwall", artist=None, uploader="Oasis - Topic"),
    )

    assert result.accepted
    assert any("artist matches (uploader/channel)" in reason for reason in result.reasons)


def test_collaboration_track_accepts_either_artist_attribution() -> None:
    track = make_track("Song Name", ["Artist", "Guest"])

    for artist in ("Artist feat. Guest", "Artist ft. Guest", "Guest & Artist"):
        assert rank_source_candidate(track, make_candidate("Song Name", artist=artist)).accepted


# ---------------------------------------------------------------------------
# Candidate scoring / source quality
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("marker_title", "expected_quality"),
    [
        pytest.param("Song Name - Official Audio", SourceQuality.OFFICIAL_AUDIO, id="audio"),
        pytest.param("Song Name (Official Lyric Video)", SourceQuality.LYRICS, id="lyric"),
        pytest.param("Song Name (Official Music Video)", SourceQuality.OFFICIAL_MV, id="mv"),
        pytest.param("Song Name Official", SourceQuality.OFFICIAL, id="official"),
        pytest.param("Song Name", SourceQuality.GENERIC, id="generic"),
    ],
)
def test_source_quality_tiers_are_classified(
    marker_title: str, expected_quality: SourceQuality
) -> None:
    assert source_profile(make_candidate(marker_title)).quality == expected_quality


def test_official_music_video_is_accepted_but_below_official_audio() -> None:
    track = make_track("Song Name")
    mv = rank_source_candidate(track, make_candidate("Song Name Official Music Video"))
    audio = rank_source_candidate(track, make_candidate("Song Name - Official Audio"))

    assert mv.accepted
    assert audio.accepted
    assert audio.score > mv.score


def test_lyric_video_is_accepted_as_a_lyric_source() -> None:
    result = rank_source_candidate(
        make_track("Wonderwall", ["Oasis"]),
        make_candidate("Wonderwall (Official Lyric Video)", artist=None, uploader="Oasis"),
    )

    assert result.accepted
    assert result.components is not None
    assert result.components.source_quality >= 10.0


# ---------------------------------------------------------------------------
# Candidate filtering / rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        pytest.param("Song Name (Live)", id="live"),
        pytest.param("Song Name Acoustic", id="acoustic"),
        pytest.param("Song Name Remix", id="remix"),
        pytest.param("Song Name (Cover)", id="cover"),
        pytest.param("Song Name Karaoke", id="karaoke"),
        pytest.param("Song Name - Speech Intro", id="speech"),
        pytest.param("Song Name Movie Scene", id="movie-scene"),
        pytest.param("Song Name Interview", id="interview"),
    ],
)
def test_wrong_version_and_non_music_candidates_are_rejected(title: str) -> None:
    result = rank_source_candidate(make_track("Song Name"), make_candidate(title))

    assert result.confidence == "rejected"
    assert not result.accepted


def test_instrumental_request_rejects_a_vocal_candidate() -> None:
    result = rank_source_candidate(
        make_track("The Beach Instrumental"),
        make_candidate("The Beach Vocal Version"),
    )

    assert result.confidence == "rejected"
    assert any("vocal version conflicts" in reason for reason in result.reasons)


def test_explicit_wrong_artist_with_similar_title_is_rejected() -> None:
    # Regression: a same-title upload by a different artist (e.g. a cover)
    # must not win on title similarity alone.
    result = rank_source_candidate(
        make_track("Wonderwall", ["Oasis"]),
        make_candidate("Wonderwall", artist="Ryan Adams", uploader="Ryan Adams"),
    )

    assert result.confidence == "rejected"
    assert any("artist" in reason for reason in result.reasons)


def test_clearly_wrong_duration_is_rejected() -> None:
    result = rank_source_candidate(
        make_track("Song Name"), make_candidate("Song Name", duration_s=400.0)
    )

    assert result.confidence == "rejected"
    assert any("duration differs" in reason for reason in result.reasons)


def test_search_layer_drops_non_music_entries() -> None:
    info = {
        "entries": [
            {"title": "Song - Official Audio", "webpage_url": "https://example.com/song"},
            {"title": "Song Reaction", "webpage_url": "https://example.com/reaction"},
            {"title": "Song Movie Scene", "webpage_url": "https://example.com/scene"},
            {"title": "Song (Live)", "webpage_url": "https://example.com/live"},
        ]
    }

    candidates = OnlineSourceSearcher._coerce_results(info)

    assert [candidate.url for candidate in candidates] == [
        "https://example.com/song",
        "https://example.com/live",
    ]


# ---------------------------------------------------------------------------
# Source validation
# ---------------------------------------------------------------------------


def test_source_validation_accepts_a_normal_studio_upload() -> None:
    verdict = validate_source_candidate(
        make_track("Song Name"), make_candidate("Song Name - Official Audio")
    )

    assert verdict.status == "accepted"
    assert verdict.accepted


def test_source_validation_tolerates_missing_metadata() -> None:
    # Regression: overly strict validation previously produced
    # "no candidate passed source validation" for uploads whose metadata was
    # simply absent. Missing metadata is not evidence of a wrong candidate.
    verdict = validate_source_candidate(
        make_track("Song Name"),
        make_candidate("Song Name", artist=None, uploader=None, duration_s=None),
    )

    assert verdict.status == "accepted"


def test_source_validation_tolerates_a_title_variant() -> None:
    # Regression: this class of candidate used to be rejected and left the
    # track with no accepted source.
    verdict = validate_source_candidate(
        make_track("Wonderwall", ["Oasis"]),
        make_candidate(
            "Oasis - Wonderwall (Official Audio)", artist=None, uploader="Oasis - Topic"
        ),
    )

    assert verdict.status == "accepted"


def test_source_validation_rejects_obvious_non_music() -> None:
    for title in (
        "Song Name (Live)",
        "Song Name (Cover)",
        "Song Name Karaoke",
        "Song Name Interview",
    ):
        verdict = validate_source_candidate(make_track("Song Name"), make_candidate(title))
        assert verdict.status == "rejected"


# ---------------------------------------------------------------------------
# Final selection
# ---------------------------------------------------------------------------


def test_selection_prefers_official_audio_over_lyric_and_video() -> None:
    track = make_track("Wonderwall", ["Oasis"])
    ranked = rank_source_candidates(
        track,
        [
            make_candidate(
                "Wonderwall Official Music Video",
                artist="Oasis",
                uploader="Oasis",
                url="https://example.com/mv",
            ),
            make_candidate(
                "Wonderwall [歌词]",
                artist="Oasis",
                uploader="Oasis",
                url="https://example.com/lyric",
            ),
            make_candidate(
                "Wonderwall - Official Audio",
                artist="Oasis",
                uploader="Oasis",
                url="https://example.com/audio",
            ),
        ],
    )

    assert ranked[0].candidate.url == "https://example.com/audio"
    assert ranked[0].confidence == "strong"
    assert all(result.accepted for result in ranked)


def test_selection_accepts_a_candidate_that_is_not_the_first_result() -> None:
    # Regression: discovery returns the best candidate in any query position;
    # selection must rank it to the top instead of taking the first result.
    track = make_track("Song Name", ["Artist"])
    ranked = rank_source_candidates(
        track,
        [
            make_candidate(
                "Unrelated Song", artist="Other", uploader="Other", url="https://example.com/first"
            ),
            make_candidate("Song Name (Live)", url="https://example.com/second"),
            make_candidate("Song Name - Official Audio", url="https://example.com/third"),
        ],
    )

    assert ranked[0].candidate.url == "https://example.com/third"
    assert ranked[0].accepted


def test_ambiguous_candidate_without_identity_evidence_is_plausible() -> None:
    result = rank_source_candidate(
        make_track("Song Name"), make_candidate("Song Name", artist=None, uploader=None)
    )

    assert result.accepted
    assert result.confidence == "plausible"


def test_no_candidate_is_accepted_when_all_are_wrong() -> None:
    ranked = rank_source_candidates(
        make_track("Song Name", ["Artist"]),
        [
            make_candidate("Song Name (Live)"),
            make_candidate("Song Name (Cover)"),
            make_candidate("Different Song", artist="Other", uploader="Other"),
            make_candidate("Song Name", artist="Another Band", uploader="Another Band"),
        ],
    )

    assert ranked
    assert all(not result.accepted for result in ranked)


# ---------------------------------------------------------------------------
# Documented regression cases
# ---------------------------------------------------------------------------


def test_regression_beyond_hai_kwo_tin_tien() -> None:
    """``海闊天空`` by Beyond.

    Traditional-script title and artist must survive normalization, and the
    correct recording must beat same-title uploads by other artists.
    """
    track = make_track("海闊天空", ["Beyond"], duration_ms=326_000)
    ranked = rank_source_candidates(
        track,
        [
            make_candidate(
                "海闊天空 (Cover)", artist="Some Cover Band", uploader="Some Cover Band"
            ),
            make_candidate(
                "Beyond - 海闊天空 (Official Music Video)",
                artist="Beyond",
                uploader="Beyond",
                duration_s=326.0,
            ),
        ],
    )

    assert ranked[0].candidate.uploader == "Beyond"
    assert ranked[0].accepted
    assert ranked[1].confidence == "rejected"


@pytest.mark.parametrize(
    ("emoji_title", "candidate_title"),
    [
        pytest.param("Higher Power ♾️", "Coldplay - Higher Power (Official Audio)", id="infinity"),
        pytest.param("My Universe ❤️", "Coldplay - My Universe (Official Audio)", id="heart"),
    ],
)
def test_regression_coldplay_emoji_titles(emoji_title: str, candidate_title: str) -> None:
    """Coldplay titles containing ``♾️`` / ``❤️``.

    Emoji must be stripped, not treated as a title difference, so the Spotify
    title still matches the plain YouTube title.
    """
    track = make_track(emoji_title, ["Coldplay"])
    candidate = make_candidate(candidate_title, artist="Coldplay", uploader="Coldplay")

    result = rank_source_candidate(track, candidate)

    assert result.accepted
    assert normalize(emoji_title).startswith("higher power") or normalize(emoji_title).startswith(
        "my universe"
    )


@pytest.mark.parametrize(
    ("symbol_title", "symbol_query", "named_title"),
    [
        pytest.param("❤️", "coldplay ❤", "Heart", id="heart"),
        pytest.param("♾️", "coldplay ♾", "Infinity", id="infinity"),
    ],
)
def test_regression_coldplay_symbol_only_titles_resolve(
    symbol_title: str, symbol_query: str, named_title: str
) -> None:
    """Coldplay's ``❤️`` / ``♾️`` singles, which are titled only with a symbol.

    Regression: with no title text to compare, *every* candidate was rejected --
    including the artist's own upload. The title is compared through the name
    the search layer expands the symbol into, so the recording is found, while a
    wrong artist and a conflicting live version are still rejected.
    """
    track = make_track(symbol_title, ["Coldplay"], duration_ms=210_000)

    for candidate in (
        make_candidate(
            f"Coldplay - {symbol_title} (Official Audio)",
            artist="Coldplay",
            uploader="Coldplay",
            source_query=symbol_query,
        ),
        make_candidate(
            f"Coldplay - {named_title} (Official Audio)",
            artist="Coldplay",
            uploader="Coldplay",
            source_query=f"coldplay {named_title.casefold()}",
        ),
    ):
        assert rank_source_candidate(track, candidate).accepted

    wrong_artist = make_candidate(
        f"Some Cover Band - {named_title}",
        artist="Some Cover Band",
        uploader="Some Cover Band",
        source_query=f"coldplay {named_title.casefold()}",
    )
    live_version = make_candidate(
        f"Coldplay - {symbol_title} (Live)", artist="Coldplay", uploader="Coldplay"
    )

    assert not rank_source_candidate(track, wrong_artist).accepted
    assert not rank_source_candidate(track, live_version).accepted


def test_regression_collaboration_track_matches_either_ordering() -> None:
    """Collaboration tracks.

    ``Artist feat. Guest`` and ``Guest & Artist`` must both confirm the
    recording identity for a track credited to ``["Artist", "Guest"]``.
    """
    track = make_track("Song Name", ["Artist", "Guest"], duration_ms=200_000)

    for candidate in (
        make_candidate("Artist feat. Guest - Song Name", artist="Artist feat. Guest"),
        make_candidate("Guest & Artist - Song Name", artist="Guest & Artist"),
    ):
        assert rank_source_candidate(track, candidate).accepted


def test_regression_strict_validation_no_candidate_passed() -> None:
    """Tracks that previously failed with ``no candidate passed source validation``.

    A candidate with a missing uploader and a bracketed ``(Official Audio)``
    title suffix must still be accepted; permissive validation only rejects on
    strong evidence of wrongness.
    """
    track = make_track("海闊天空", ["Beyond"], duration_ms=326_000)
    candidate = make_candidate(
        "海阔天空 (Official Audio)", artist="Beyond", uploader=None, duration_s=None
    )

    verdict = validate_source_candidate(track, candidate)

    assert verdict.status == "accepted"
