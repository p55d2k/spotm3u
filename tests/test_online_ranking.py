"""Online ranking: which candidates are usable, and in what order.

Three concerns are kept apart on purpose. This file covers the ranking
decision itself: what is accepted or rejected, how candidates are ordered and
what is recorded as the reason. Validating the *chosen* source is
``test_online_validation.py``, and preferring one upload kind over another
(official audio, lyric, music video) once the recording is identified is
``test_online_source_selection.py``.
"""

import pytest

from spotm3u.models import Track
from spotm3u.online import (
    CandidateRanking,
    SourceCandidate,
    rank_source_candidate,
    rank_source_candidates,
)

TRACK = Track(title="Song Name", artists=["Artist"], duration_ms=210_000)


def candidate(**overrides) -> SourceCandidate:
    values = {
        "url": "https://example.com/song",
        "title": "Song Name",
        "artist": "Artist",
        "uploader": "Artist",
        "duration_s": 210.0,
        "source_type": "youtube",
    }
    values.update(overrides)
    return SourceCandidate(**values)


# --- recording identity -------------------------------------------------------


def test_exact_title_and_artist_is_a_strong_match() -> None:
    result = rank_source_candidate(TRACK, candidate())

    assert isinstance(result, CandidateRanking)
    assert result.confidence == "strong"
    assert result.accepted
    assert 0 <= result.score <= 100
    assert result.confidence in {"strong", "plausible", "uncertain", "rejected"}


def test_exact_studio_recording_beats_unrelated_song() -> None:
    ranked = rank_source_candidates(
        TRACK,
        [
            candidate(title="Unrelated Song", artist="Other Artist", uploader="Other Artist"),
            candidate(title="Song Name - Official Audio"),
        ],
    )

    assert ranked[0].confidence == "strong"
    assert ranked[0].accepted


@pytest.mark.parametrize(
    "title",
    [
        pytest.param("Song Name.", id="trailing-punctuation"),
        pytest.param("Song Name - Official Audio", id="official-audio"),
        pytest.param("Song Name (Remastered)", id="remastered"),
        pytest.param("Song Name - Remastered 2011", id="remastered-year"),
        pytest.param("Song Name - Original Studio Recording", id="studio-recording"),
        pytest.param("Song Name [Official]", id="bracketed-official"),
    ],
)
def test_imperfect_title_variants_are_accepted(title: str) -> None:
    assert rank_source_candidate(TRACK, candidate(title=title)).accepted


@pytest.mark.parametrize(
    ("requested_title", "candidate_title"),
    [
        pytest.param("Song Name (Remastered)", "Song Name - Official Audio", id="remastered"),
        pytest.param("Song Name (Live)", "Song Name (Live from Knebworth)", id="live"),
    ],
)
def test_a_variant_the_track_itself_asks_for_is_accepted(
    requested_title: str, candidate_title: str
) -> None:
    track = Track(title=requested_title, artists=["Artist"], duration_ms=210_000)

    assert rank_source_candidate(track, candidate(title=candidate_title)).accepted


def test_featured_artists_and_feat_spellings_are_tolerated() -> None:
    track = Track(title="Song Name", artists=["Artist", "Guest"], duration_ms=210_000)

    assert rank_source_candidate(track, candidate()).accepted
    for artist in ("Artist feat. Guest", "Artist ft. Guest", "Artist featuring Guest"):
        assert rank_source_candidate(track, candidate(artist=artist)).accepted


def test_uploader_without_a_track_artist_is_not_rejected() -> None:
    result = rank_source_candidate(TRACK, candidate(artist=None, uploader="Random Channel"))

    assert result.accepted
    assert any("uploader differs" in reason for reason in result.reasons)


def test_artist_attribution_inside_the_title_is_not_a_title_difference() -> None:
    ranked = rank_source_candidates(TRACK, [candidate(), candidate(title="Artist Song Name")])

    assert all(result.accepted for result in ranked)
    assert all(result.components is not None and result.components.title > 0.5 for result in ranked)


# --- rejection -----------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"title": "Song Name (Live)"}, id="live"),
        pytest.param({"title": "Song Name Remix"}, id="remix"),
        pytest.param({"title": "Song Name Sped-Up"}, id="sped-up"),
        pytest.param({"title": "Song Name (Cover)"}, id="cover"),
        pytest.param({"title": "Unrelated Song"}, id="unrelated-song"),
        pytest.param({"artist": "Another Band", "uploader": "Another Band"}, id="wrong-artist"),
    ],
)
def test_alternate_recordings_are_rejected(overrides: dict) -> None:
    result = rank_source_candidate(TRACK, candidate(**overrides))

    assert result.confidence == "rejected"
    assert not result.accepted


def test_rejection_reasons_name_the_mismatch() -> None:
    live = rank_source_candidate(TRACK, candidate(title="Song Name (Live)"))
    assert any("live" in reason.casefold() for reason in live.reasons)

    duration = rank_source_candidate(TRACK, candidate(duration_s=400.0))
    assert duration.confidence == "rejected"
    assert any("duration differs" in reason for reason in duration.reasons)


def test_clearly_wrong_duration_is_rejected() -> None:
    result = rank_source_candidate(TRACK, candidate(duration_s=400))

    assert result.confidence == "rejected"
    assert not result.accepted


def test_remix_does_not_supplant_the_original() -> None:
    ranked = rank_source_candidates(TRACK, [candidate(title="Song Name Remix"), candidate()])

    assert ranked[0].accepted
    assert ranked[0].candidate.title == "Song Name"
    assert ranked[1].candidate.title == "Song Name Remix"
    assert ranked[1].confidence == "rejected"


def test_instrumental_request_rejects_vocal_version() -> None:
    track = Track("Song Name Instrumental", ["Artist"], duration_ms=210_000)
    result = rank_source_candidate(track, candidate(title="Song Name Vocal Version"))

    assert result.confidence == "rejected"
    assert any("vocal version conflicts" in reason for reason in result.reasons)


# --- tolerances ----------------------------------------------------------------


def test_missing_duration_and_uploader_remain_plausible() -> None:
    result = rank_source_candidate(TRACK, candidate(uploader=None, duration_s=None))

    assert result.accepted
    assert result.confidence in {"strong", "plausible"}
    assert any("duration is unavailable" in reason for reason in result.reasons)


def test_slight_duration_difference_is_not_rejected() -> None:
    assert rank_source_candidate(TRACK, candidate(duration_s=212.0)).accepted


def test_music_video_is_penalized_but_not_rejected() -> None:
    result = rank_source_candidate(TRACK, candidate(title="Song Name Official Music Video"))

    assert result.confidence in {"plausible", "uncertain"}
    assert result.accepted
    assert any("music video" in reason for reason in result.reasons)

    audio = rank_source_candidate(TRACK, candidate(title="Song Name - Official Audio"))
    assert audio.score > result.score


def test_instrumental_candidate_beats_unmarked_candidate() -> None:
    track = Track("The Beach - Instrumental", ["Artist"], duration_ms=210_000)
    ranked = rank_source_candidates(
        track,
        [
            candidate(title="The Beach"),
            candidate(title="The Beach - Instrumental"),
        ],
    )

    assert ranked[0].candidate.title.endswith("Instrumental")
    assert ranked[0].accepted
