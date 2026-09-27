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


# --- title analysis: source labels vs collaborators vs versions ----------------


def test_source_annotation_is_not_part_of_the_title_core() -> None:
    """``Official Audio`` describes the upload, not the recording."""
    from spotm3u.online.ranking import split_title

    plain = split_title("Love Me Not")
    labelled = split_title("Love Me Not (Official Audio)")

    assert plain.core == labelled.core == "love me not"
    assert labelled.collaborations == frozenset()
    assert labelled.versions == frozenset()


@pytest.mark.parametrize(
    "title",
    [
        "Love Me Not (feat. Rex Orange County)",
        "Love Me Not (feat. Rex Orange County) (Official Audio)",
        "Love Me Not (Official Audio) ft. Rex Orange County",
        "Love Me Not ft Rex Orange County",
    ],
)
def test_credited_collaborator_is_lifted_out_of_the_title(title: str) -> None:
    from spotm3u.online.ranking import split_title

    parts = split_title(title)

    assert parts.core == "love me not"
    assert parts.collaborations == frozenset({"rex orange county"})


def test_multiple_collaborators_are_separated() -> None:
    from spotm3u.online.ranking import split_title

    parts = split_title("Song (feat. Anna & Ben)")

    assert parts.core == "song"
    assert parts.collaborations == frozenset({"anna", "ben"})


def test_bare_with_in_a_title_is_not_a_collaboration() -> None:
    """``Dance With Me`` is English, not a credit; only bracketed ``with`` is."""
    from spotm3u.online.ranking import split_title

    assert split_title("Dance With Me").collaborations == frozenset()
    assert split_title("Song (with Anna)").collaborations == frozenset({"anna"})


def test_featured_artist_prefix_stops_at_the_title_separator() -> None:
    """``Artist feat. Guest - Song Name`` credits ``Guest``, not ``Guest - ...``."""
    from spotm3u.online.ranking import split_title

    parts = split_title("Artist feat. Guest - Song Name")

    assert parts.collaborations == frozenset({"guest"})
    assert parts.core == "artist song name"


def test_version_modifier_is_kept_out_of_the_core_and_recorded() -> None:
    from spotm3u.online.ranking import split_title

    parts = split_title("Song Name (Remix)")

    assert parts.core == "song name"
    assert "remix" in parts.versions


def test_unknown_parenthetical_is_not_silently_discarded() -> None:
    from spotm3u.online.ranking import split_title

    parts = split_title("Song Name (Anniversary Edition)")

    assert "anniversary edition" in parts.core
    assert parts.collaborations == frozenset()


# --- symbol identity is not search expansion -----------------------------------


def test_symbol_title_does_not_match_a_different_artist_through_the_alias() -> None:
    """``♾️`` expands to ``infinity`` for *search*, which is not proof of identity.

    Regression: Coldplay's ``♾️`` resolved to James Young's ``Infinity`` because
    both sides were compared through the expanded name and the unrelated artist
    was never required to show up in the candidate at all.
    """
    track = Track("♾️", ["Coldplay"], duration_ms=210_000)
    wrong = candidate(
        title="Infinity", artist="James Young", uploader="James Young", duration_s=200.0
    )

    result = rank_source_candidate(track, wrong)

    assert not result.accepted
    assert any("search alias" in reason for reason in result.reasons)


@pytest.mark.parametrize(
    "title", ["Coldplay - ♾️ (Official Audio)", "Coldplay - Infinity (Official Audio)"]
)
def test_symbol_title_still_resolves_the_requesting_artist(title: str) -> None:
    """The artist's own upload is still found through the alias."""
    track = Track("♾️", ["Coldplay"], duration_ms=210_000)

    result = rank_source_candidate(
        track, candidate(title=title, artist="Coldplay", uploader="Coldplay", duration_s=200.0)
    )

    assert result.accepted


# --- collaboration identity ----------------------------------------------------


def test_unrequested_collaborator_is_rejected() -> None:
    """An extra credited performer can be a different recording.

    Regression: token containment read ``love me not`` as a subset of
    ``love me not feat rex orange county`` and scored the candidate a perfect
    title match, so the collaboration scored 100 and was accepted as ``strong``.
    """
    track = Track("Love Me Not", ["Ravyn Lenae"], duration_ms=200_000)
    featured = candidate(
        title="Love Me Not (feat. Rex Orange County) (Official Audio)",
        artist="Ravyn Lenae",
        uploader="Ravyn Lenae",
        duration_s=200.0,
    )

    result = rank_source_candidate(track, featured)

    assert not result.accepted
    assert any("unrequested artist" in reason for reason in result.reasons)


def test_plain_single_still_matches_its_own_upload() -> None:
    track = Track("Love Me Not", ["Ravyn Lenae"], duration_ms=200_000)

    result = rank_source_candidate(
        track,
        candidate(
            title="Love Me Not (Official Audio)",
            artist="Ravyn Lenae",
            uploader="Ravyn Lenae",
            duration_s=200.0,
        ),
    )

    assert result.accepted


@pytest.mark.parametrize(
    "title",
    [
        "Love Me Not (feat. Rex Orange County) (Official Audio)",
        "Love Me Not (Official Audio) ft. Rex Orange County",
        "Love Me Not ft Rex Orange County",
    ],
)
def test_collaboration_matches_when_both_sides_agree(title: str) -> None:
    track = Track("Love Me Not (feat. Rex Orange County)", ["Ravyn Lenae"], duration_ms=200_000)

    result = rank_source_candidate(
        track,
        candidate(title=title, artist="Ravyn Lenae", uploader="Ravyn Lenae", duration_s=200.0),
    )

    assert result.accepted


def test_collaborator_listed_in_the_requested_artists_is_allowed() -> None:
    """Spotify credits both sides, so the guest need not appear in the title."""
    track = Track("Love Me Not", ["Ravyn Lenae", "Rex Orange County"], duration_ms=200_000)

    result = rank_source_candidate(
        track,
        candidate(
            title="Love Me Not (feat. Rex Orange County) (Official Audio)",
            artist="Ravyn Lenae",
            uploader="Ravyn Lenae",
            duration_s=200.0,
        ),
    )

    assert result.accepted


def test_both_artists_in_the_candidate_title_is_not_a_collaboration_conflict() -> None:
    """``Guest & Artist - Song`` is handled by artist evidence, not a credit."""
    track = Track("Song Name", ["Artist", "Guest"], duration_ms=200_000)

    result = rank_source_candidate(
        track, candidate(title="Guest & Artist - Song Name", artist="Guest & Artist")
    )

    assert result.accepted
