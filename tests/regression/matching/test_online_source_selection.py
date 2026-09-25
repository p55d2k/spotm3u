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
            "薛之谦 演员 official audio": [
                {
                    "title": "演员",
                    "webpage_url": "https://example.com/later-query-result",
                    "uploader": "薛之谦",
                    "duration": 250,
                }
            ],
        }
    )

    candidates = OnlineSourceSearcher(max_results=5).search(ACTOR)

    assert any(c.source_query == "薛之谦 演员 official audio" for c in candidates)
    assert any(c.url == "https://example.com/later-query-result" for c in candidates)


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


def test_candidate_scores_carry_diagnostics() -> None:
    lyric = joker(title="演员 [歌词]")
    result = rank_source_candidates(ACTOR, [lyric])[0]
    assert result.components is not None
    assert result.components.identity > 0.0  # artist identity is scored
    assert result.components.source_quality >= 10.0  # lyric tier contribution
    assert any("source quality" in reason for reason in result.reasons)
    assert any("artist matches" in reason for reason in result.reasons)


def test_lyric_video_variant_is_treated_as_lyric_source() -> None:
    result = rank_source_candidates(TRACK, [candidate(title="Wonderwall (Official Lyric Video)")])[
        0
    ]
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
