from spotm3u.models import Track
from spotm3u.online import (
    CandidateRanking,
    SourceCandidate,
    rank_source_candidates,
    rank_source_candidate,
)


def candidate(title: str, artist: str, duration: float = 210) -> SourceCandidate:
    return SourceCandidate(
        url=f"https://example.com/{title}",
        title=title,
        artist=artist,
        uploader=artist,
        duration_s=duration,
        source_type="youtube",
    )


TRACK = Track(title="Song Name", artists=["Artist"], duration_ms=210_000)


def test_exact_studio_recording_beats_unrelated_song() -> None:
    ranked = rank_source_candidates(
        TRACK,
        [candidate("Unrelated Song", "Other Artist"), candidate("Song Name - Official Audio", "Artist")],
    )
    assert ranked[0].confidence == "strong"
    assert ranked[0].accepted


def test_alternate_recordings_are_not_accepted() -> None:
    for title in ("Song Name Official Music Video", "Song Name (Live)", "Song Name Remix",
                  "Song Name Sped-Up", "Song Name (Cover)"):
        result = rank_source_candidate(TRACK, candidate(title, "Artist"))
        assert result.confidence == "rejected"
        assert not result.accepted


def test_exact_title_with_poor_artist_is_rejected() -> None:
    result = rank_source_candidate(TRACK, candidate("Song Name", "Wrong Artist"))
    assert result.confidence == "rejected"


def test_wrong_duration_remains_uncertain_or_rejected() -> None:
    result = rank_source_candidate(TRACK, candidate("Song Name", "Artist", duration=400))
    assert result.confidence != "strong"
    assert not result.accepted


def test_ranking_result_is_structured() -> None:
    result = rank_source_candidate(TRACK, candidate("Song Name", "Artist"))
    assert isinstance(result, CandidateRanking)
    assert 0 <= result.score <= 100
    assert result.confidence in {"strong", "uncertain", "rejected"}
