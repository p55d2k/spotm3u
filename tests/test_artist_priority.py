"""Artist identity must be a first-class online matching signal.

Regression: for Joker Xue's ``演员`` the pipeline downloaded Hebe Tien's cover
because both share the title. Artist identity must outweigh title similarity
for common titles, while keeping the permissive matching philosophy.
"""

from spotm3u.models import Track
from spotm3u.online import (
    SourceCandidate,
    build_search_queries,
    rank_source_candidate,
    rank_source_candidates,
)
from spotm3u.online.search import OnlineSourceSearcher

ACTOR = Track(title="演员", artists=["薛之谦"], duration_ms=250_000)


def joker(
    *, title: str = "演员 Official MV", artist: str = "", channel: str = "薛之谦"
) -> SourceCandidate:
    return SourceCandidate(
        url="https://example.com/joker",
        title=title,
        artist=artist or None,
        uploader=channel,
        duration_s=250.0,
        source_type="youtube",
    )


def hebe(
    *, title: str = "演员", artist: str = "", channel: str = "田馥甄 HebeTien"
) -> SourceCandidate:
    return SourceCandidate(
        url="https://example.com/hebe",
        title=title,
        artist=artist or None,
        uploader=channel,
        duration_s=250.0,
        source_type="youtube",
    )


def test_actor_official_upload_preferred_over_cover() -> None:
    ranked = rank_source_candidates(ACTOR, [hebe(), joker()])
    assert ranked[0].candidate.url == "https://example.com/joker"
    assert ranked[0].accepted
    assert ranked[0].score > ranked[1].score
    cover = rank_source_candidate(ACTOR, hebe())
    assert cover.accepted or cover.confidence == "rejected"


def test_same_title_wrong_explicit_artist_is_rejected() -> None:
    result = rank_source_candidate(ACTOR, hebe(artist="Hebe Tien", channel="Hebe Tien"))
    assert result.confidence == "rejected"
    assert not result.accepted
    assert any("conflicts" in reason for reason in result.reasons)


def test_correct_artist_official_mv_is_accepted_as_plausible() -> None:
    mv = rank_source_candidate(ACTOR, joker(title="演员 Official MV"))
    audio = rank_source_candidate(ACTOR, joker(title="演员 Official Audio"))
    for result in (mv, audio):
        assert result.accepted
        assert result.confidence in {"strong", "plausible"}


def test_correct_artist_with_feat_ft_with_is_accepted() -> None:
    for artist in (
        "薛之谦 feat. 郭顶",
        "薛之谦 ft. 郭顶",
        "薛之谦 with 郭顶",
    ):
        result = rank_source_candidate(
            ACTOR,
            SourceCandidate(
                url="https://example.com/feat",
                title="演员",
                artist=artist,
                uploader="随便频道",
                duration_s=250.0,
                source_type="youtube",
            ),
        )
        assert result.accepted, artist


def test_correct_artist_but_different_uploader_is_possible() -> None:
    explicit = rank_source_candidate(
        ACTOR,
        SourceCandidate(
            url="https://example.com/a",
            title="演员",
            artist="薛之谦",
            uploader="随便频道",
            duration_s=250.0,
            source_type="youtube",
        ),
    )
    title_attribution = rank_source_candidate(
        ACTOR,
        SourceCandidate(
            url="https://example.com/b",
            title="演员 - 薛之谦",
            artist=None,
            uploader="随便频道",
            duration_s=250.0,
            source_type="youtube",
        ),
    )
    assert explicit.accepted
    assert title_attribution.accepted


def test_wrong_artist_with_exact_title_does_not_win() -> None:
    correct = joker(title="演员", channel="薛之谦官方频道")
    wrong_uploader = hebe()
    ranked = rank_source_candidates(ACTOR, [wrong_uploader, correct])
    assert ranked[0].candidate.url == correct.url

    wrong_explicit = hebe(artist="Hebe Tien", channel="Hebe Tien")
    ranked_with_explicit = rank_source_candidates(ACTOR, [wrong_explicit, correct])
    assert ranked_with_explicit[0].candidate.url == correct.url
    assert rank_source_candidate(ACTOR, wrong_explicit).confidence == "rejected"


def test_explicit_cover_by_other_artist_is_rejected() -> None:
    for title in ("演员 (翻唱)", "演员 (cover)", "演员 Cover by 田馥甄"):
        result = rank_source_candidate(ACTOR, hebe(title=title))
        assert result.confidence == "rejected", title

    cover = rank_source_candidate(ACTOR, hebe(title="演员 (翻唱)"))
    assert not cover.accepted
    assert any("alternate version" in reason for reason in cover.reasons)


def test_search_queries_include_requested_artist() -> None:
    queries = build_search_queries(ACTOR)
    joined = " ".join(queries)
    assert "薛之谦" in joined
    assert "演员" in joined


def test_coerce_results_keeps_official_mv_title_and_uploader() -> None:
    info = {
        "entries": [
            {
                "title": "演员 Official MV",
                "webpage_url": "https://example.com/joker",
                "uploader": "薛之谦",
                "duration": 250,
            },
        ]
    }
    candidates = OnlineSourceSearcher._coerce_results(info)
    assert len(candidates) == 1
    assert candidates[0].uploader == "薛之谦"
