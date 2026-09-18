"""Source selection: identity first, then source-quality preference.

Regression: for the Joker Xue ``演员`` case the pipeline downloaded Hebe
Tien's same-title cover, and for other songs it favored exact-title uploads
regardless of upload kind. Selection must (1) identify the correct recording
by artist before ranking, then (2) prefer the best audio source so educated
on recording identity.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from spotm3u.models import Track
from spotm3u.online import (
    SourceCandidate,
    build_search_queries,
    rank_source_candidates,
)
from spotm3u.online.search import OnlineSourceSearcher

ACTOR = Track(title="演员", artists=["薛之谦"], duration_ms=250_000)
TRACK = Track(title="Wonderwall", artists=["Oasis"], duration_ms=258_000)


def joker(**overrides) -> SourceCandidate:
    values = {
        "url": "https://example.com/joker",
        "title": "演员 Official MV",
        "uploader": "薛之谦",
        "duration_s": 250.0,
        "source_type": "youtube",
    }
    values.update(overrides)
    return SourceCandidate(**values)


def hebe(**overrides) -> SourceCandidate:
    values = {
        "url": "https://example.com/hebe",
        "title": "演员",
        "uploader": "田馥甄 HebeTien",
        "duration_s": 250.0,
        "source_type": "youtube",
    }
    values.update(overrides)
    return SourceCandidate(**values)


def candidate(**overrides) -> SourceCandidate:
    values = {
        "url": "https://example.com/wonderwall",
        "title": "Wonderwall",
        "uploader": "Oasis",
        "duration_s": 258.0,
        "source_type": "youtube",
    }
    values.update(overrides)
    return SourceCandidate(**values)


def test_actor_loses_to_joker_when_title_matches_exactly() -> None:
    ranked = rank_source_candidates(ACTOR, [hebe(), joker()])
    assert ranked[0].candidate.url == "https://example.com/joker"
    assert ranked[0].components is not None
    assert ranked[0].components.identity > ranked[1].components.identity


def test_lyric_and_audio_beat_mv_for_same_recording() -> None:
    mv = joker(title="演员 Official MV")
    lyric = joker(title="演员 [歌词]")
    audio = joker(title="演员 Official Audio")
    ranked = rank_source_candidates(ACTOR, [mv, lyric, audio])
    assert ranked[0].candidate.url == audio.url
    assert ranked[1].candidate.url == lyric.url
    assert ranked[2].candidate.url == mv.url
    assert all(r.accepted for r in ranked)
    assert ranked[0].confidence == "strong"
    assert ranked[2].confidence == "plausible" or ranked[2].confidence == "strong"


def test_mv_is_a_valid_fallback() -> None:
    result = rank_source_candidates(ACTOR, [joker()])[0]
    assert result.accepted
    assert any("music video" in reason for reason in result.reasons)


def test_same_title_wrong_artist_is_rejected() -> None:
    result = rank_source_candidates(ACTOR, [hebe(artist="Hebe Tien", uploader="Hebe Tien")])[0]
    assert result.confidence == "rejected"
    assert not result.accepted


def test_official_audio_preferred_over_lyric_and_mv() -> None:
    ranked = rank_source_candidates(
        TRACK,
        [
            candidate(title="Wonderwall Official Music Video"),
            candidate(title="Wonderwall [歌词]"),
            candidate(title="Wonderwall - Official Audio"),
        ],
    )
    assert ranked[0].candidate.title == "Wonderwall - Official Audio"
    assert ranked[0].confidence == "strong"


def test_results_in_second_or_third_query_are_discovered(monkeypatch: pytest.MonkeyPatch) -> None:
    def install(entries_by_query):
        class DownloadError(Exception):
            pass

        class YoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, query, download=False):
                key = query.split(":", 1)[1] if ":" in query else query
                return {"entries": entries_by_query.get(key, [])}

        module = ModuleType("yt_dlp")
        module.utils = SimpleNamespace(DownloadError=DownloadError)
        module.YoutubeDL = YoutubeDL
        monkeypatch.setitem(sys.modules, "yt_dlp", module)

    install(
        {
            "薛之谦 演员": [],
            "薛之谦 演员 lyric": [
                {
                    "title": "演员",
                    "webpage_url": "https://example.com/lyric-result",
                    "uploader": "薛之谦",
                    "duration": 250,
                }
            ],
        }
    )

    candidates = OnlineSourceSearcher(max_results=5).search(ACTOR)

    assert any(c.source_query == "薛之谦 演员 lyric" for c in candidates)
    assert any(c.url == "https://example.com/lyric-result" for c in candidates)


def test_duplicate_across_queries_is_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    class DownloadError(Exception):
        pass

    class YoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, query, download=False):
            return {
                "entries": [
                    {
                        "title": "演员",
                        "webpage_url": "https://example.com/dup",
                        "uploader": "薛之谦",
                        "duration": 250,
                    }
                ]
            }

    module = ModuleType("yt_dlp")
    module.utils = SimpleNamespace(DownloadError=DownloadError)
    module.YoutubeDL = YoutubeDL
    monkeypatch.setitem(sys.modules, "yt_dlp", module)

    candidates = OnlineSourceSearcher(max_results=5).search(ACTOR)

    assert [c.url for c in candidates] == ["https://example.com/dup"]


def test_featured_artist_variants_are_accepted() -> None:
    track = Track(title="演员", artists=["薛之谦", "郭顶"], duration_ms=250_000)
    for artist in ("薛之谦 feat. 郭顶", "薛之谦 ft. 郭顶"):
        candidate_ = joker(title="演员", artist=artist, uploader="随便频道")
        result = rank_source_candidates(track, [candidate_])[0]
        assert result.accepted, artist


def test_explicit_cover_by_other_artist_is_rejected() -> None:
    for title in ("演员 (翻唱)", "演员 Cover by 田馥甄"):
        result = rank_source_candidates(ACTOR, [hebe(title=title)])[0]
        assert result.confidence == "rejected", title
        assert any("alternate version" in reason for reason in result.reasons)


def test_live_beats_studio_only_when_requested_live() -> None:
    studio = Track(title="Wonderwall", artists=["Oasis"], duration_ms=258_000)
    live = Track(title="Wonderwall (Live)", artists=["Oasis"], duration_ms=258_000)

    live_candidate = candidate(title="Wonderwall (Live from Knebworth)")
    studio_candidate = candidate(title="Wonderwall (Remastered)")

    assert rank_source_candidates(studio, [live_candidate])[0].confidence == "rejected"
    assert rank_source_candidates(live, [live_candidate])[0].accepted


def test_remix_does_not_supplant_original() -> None:
    result = rank_source_candidates(TRACK, [candidate(title="Wonderwall Remix")])[0]
    assert result.confidence == "rejected"


def test_null_duration_does_not_get_rejected_by_default() -> None:
    incomplete = candidate(duration_s=None, uploader=None)
    result = rank_source_candidates(TRACK, [incomplete])[0]
    assert result.accepted


def test_candidate_scores_carry_diagnostics() -> None:
    lyric = joker(title="演员 [歌词]")
    result = rank_source_candidates(ACTOR, [lyric])[0]
    assert result.components is not None
    assert result.components.identity > 0.0  # artist identity is scored
    assert result.components.source_quality >= 10.0  # lyric tier contribution
    assert any("source quality" in reason for reason in result.reasons)
    assert any("artist matches" in reason for reason in result.reasons)


def test_rejected_reason_is_recorded() -> None:
    result = rank_source_candidates(TRACK, [candidate(title="Wonderwall (Live)")])[0]
    assert result.confidence == "rejected"
    assert any("live" in reason.casefold() for reason in result.reasons)


def test_rejected_candidate_for_wrong_duration() -> None:
    result = rank_source_candidates(
        TRACK, [candidate(duration_s=400.0)]
    )[0]
    assert result.confidence == "rejected"
    assert any("duration differs" in reason for reason in result.reasons)


def test_lyric_video_variant_is_treated_as_lyric_source() -> None:
    result = rank_source_candidates(
        TRACK, [candidate(title="Wonderwall (Official Lyric Video)")]
    )[0]
    assert result.accepted
    assert result.components.source_quality >= 10.0


def test_traditional_script_titles_match_simplified_request() -> None:
    traditional = joker(title="薛之謙 Joker Xue【演員】Official Music Video")
    result = rank_source_candidates(ACTOR, [traditional])[0]
    assert result.accepted
    assert result.components is not None
    assert result.components.title > 0.0
    assert any("artist matches" in reason for reason in result.reasons)


def test_traditional_script_artist_is_recognized_in_uploader() -> None:
    traditional = joker(title="演员", uploader="薛之謙 JokerXue", artist="薛之謙")
    result = rank_source_candidates(ACTOR, [traditional])[0]
    assert result.accepted
    assert result.components is not None
    assert result.components.identity > 0.0
    assert any("artist matches" in reason for reason in result.reasons)


def test_traditional_script_lyric_marker_is_a_lyric_source() -> None:
    traditional_lyric = joker(title="薛之謙 / 演員【歌詞】")
    result = rank_source_candidates(ACTOR, [traditional_lyric])[0]
    assert result.accepted
    assert result.components is not None
    assert result.components.source_quality >= 10.0
    assert any("source quality" in reason for reason in result.reasons)


def test_traditional_script_joker_beats_bare_title_hebe() -> None:
    traditional_mv = joker(
        url="https://example.com/trad-mv",
        title="薛之謙 Joker Xue【演員】Official Music Video",
        uploader="薛之謙 JokerXue",
        duration_s=299.0,
    )
    bare_hebe = hebe(duration_s=250.0)
    ranked = rank_source_candidates(ACTOR, [bare_hebe, traditional_mv])
    assert ranked[0].candidate.url == traditional_mv.url
    assert ranked[0].components is not None
    assert ranked[0].components.identity > ranked[1].components.identity
    assert ranked[0].accepted


def test_artist_attribution_in_title_is_not_a_title_difference() -> None:
    bare = joker(title="演员")
    with_artist = joker(title="薛之谦 演员")
    both = rank_source_candidates(ACTOR, [bare, with_artist])
    assert all(r.accepted for r in both)
    assert (
        both[0].components is not None
        and both[1].components is not None
        and both[0].components.title > 0.5
        and both[1].components.title > 0.5
    )