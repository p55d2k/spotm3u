"""Permissive online source matching test matrix.

Every legitimate candidate below must reach download/audio validation instead
of being rejected for imperfect metadata. Obvious wrong recordings are still
rejected.
"""

from spotm3u.models import Track
from spotm3u.online import SourceCandidate, rank_source_candidate, validate_source_candidate

TRACK = Track(title="Wonderwall", artists=["Oasis"], duration_ms=258_000)


def source(**overrides) -> SourceCandidate:
    values = {
        "url": "https://example.com/wonderwall",
        "title": "Wonderwall",
        "artist": "Oasis",
        "uploader": "Oasis",
        "duration_s": 258.0,
        "source_type": "youtube",
    }
    values.update(overrides)
    return SourceCandidate(**values)


def test_exact_title_and_exact_artist() -> None:
    result = rank_source_candidate(TRACK, source())
    assert result.confidence == "strong"
    assert result.accepted


def test_title_with_punctuation_differences() -> None:
    assert rank_source_candidate(TRACK, source(title="Wonderwall.")).accepted
    assert rank_source_candidate(TRACK, source(title="Wonderwall - Official Audio")).accepted


def test_title_with_remastered_marker() -> None:
    assert rank_source_candidate(TRACK, source(title="Wonderwall (Remastered)")).accepted
    assert rank_source_candidate(TRACK, source(title="Wonderwall - Remastered 2011")).accepted


def test_requested_remastered_matches_plain_title() -> None:
    track = Track(title="Wonderwall (Remastered)", artists=["Oasis"], duration_ms=258_000)
    assert rank_source_candidate(track, source(title="Wonderwall - Official Audio")).accepted


def test_missing_duration_is_not_rejected() -> None:
    result = rank_source_candidate(TRACK, source(duration_s=None))
    assert result.accepted
    assert any("duration is unavailable" in reason for reason in result.reasons)


def test_slight_duration_difference_is_not_rejected() -> None:
    assert rank_source_candidate(TRACK, source(duration_s=260.0)).accepted


def test_different_uploader_is_not_rejected() -> None:
    result = rank_source_candidate(TRACK, source(artist=None, uploader="Random Channel"))
    assert result.accepted
    assert any("uploader differs" in reason for reason in result.reasons)


def test_featured_artists_are_tolerated() -> None:
    track = Track(title="Wonderwall", artists=["Oasis", "Noel Gallagher"], duration_ms=258_000)
    assert rank_source_candidate(track, source(artist="Oasis")).accepted


def test_feat_formatting_variants_are_tolerated() -> None:
    track = Track(title="Wonderwall", artists=["Oasis", "Noel Gallagher"], duration_ms=258_000)
    for artist in (
        "Oasis feat. Noel Gallagher",
        "Oasis ft. Noel Gallagher",
        "Oasis featuring Noel Gallagher",
    ):
        assert rank_source_candidate(track, source(artist=artist)).accepted


def test_music_video_candidate_is_preferred_over_generic_but_below_audio() -> None:
    mv = rank_source_candidate(TRACK, source(title="Wonderwall Official Music Video"))
    assert mv.accepted
    assert any("music video" in reason for reason in mv.reasons)

    generic = rank_source_candidate(TRACK, source())
    assert mv.score >= generic.score

    audio = rank_source_candidate(TRACK, source(title="Wonderwall - Official Audio"))
    assert audio.accepted
    assert audio.score > mv.score


def test_live_candidate_is_rejected() -> None:
    assert (
        rank_source_candidate(TRACK, source(title="Wonderwall (Live at Knebworth)")).confidence
        == "rejected"
    )


def test_remix_candidate_is_rejected() -> None:
    assert rank_source_candidate(TRACK, source(title="Wonderwall Remix")).confidence == "rejected"


def test_cover_candidate_is_rejected() -> None:
    assert (
        rank_source_candidate(TRACK, source(title="Wonderwall (Cover by Fan)")).confidence
        == "rejected"
    )


def test_movie_scene_candidate_is_rejected() -> None:
    assert (
        rank_source_candidate(TRACK, source(title="Wonderwall Movie Scene")).confidence
        == "rejected"
    )


def test_obvious_unrelated_song_is_rejected() -> None:
    assert (
        rank_source_candidate(TRACK, source(title="Champagne Supernova")).confidence == "rejected"
    )


def test_incomplete_metadata_candidate_is_accepted() -> None:
    result = rank_source_candidate(
        TRACK, source(title="Wonderwall", artist=None, uploader=None, duration_s=None)
    )
    assert result.accepted
    assert (
        validate_source_candidate(
            TRACK, source(title="Wonderwall", artist=None, uploader=None, duration_s=None)
        ).status
        == "accepted"
    )


def test_harmless_extra_title_text_is_accepted() -> None:
    assert rank_source_candidate(
        TRACK, source(title="Wonderwall - Original Studio Recording")
    ).accepted
    assert rank_source_candidate(TRACK, source(title="Wonderwall [Official]")).accepted


def test_wrong_artist_is_rejected() -> None:
    assert (
        rank_source_candidate(
            TRACK, source(title="Wonderwall", artist="Another Band", uploader="Another Band")
        ).confidence
        == "rejected"
    )
