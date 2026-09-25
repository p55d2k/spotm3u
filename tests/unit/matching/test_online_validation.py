from spotm3u.models import Track
from spotm3u.online import SourceCandidate, validate_source_candidate

TRACK = Track(title="Song Name", artists=["Artist"], duration_ms=210_000)


def source(**kwargs) -> SourceCandidate:
    values = {
        "url": "https://example.com/song",
        "title": "Song Name - Official Audio",
        "artist": "Artist",
        "uploader": "Artist",
        "duration_s": 210,
        "source_type": "youtube",
    }
    values.update(kwargs)
    return SourceCandidate(**values)


def test_normal_studio_source_is_accepted() -> None:
    assert validate_source_candidate(TRACK, source()).status == "accepted"


def test_obvious_unsuitable_versions_are_rejected() -> None:
    for title in (
        "Song Name (Live)",
        "Song Name Remix",
        "Song Name (Cover)",
        "Song Name Movie Scene",
        "Song Name Karaoke",
        "Song Name Interview",
    ):
        assert validate_source_candidate(TRACK, source(title=title)).status == "rejected"


def test_music_video_is_not_rejected() -> None:
    assert (
        validate_source_candidate(TRACK, source(title="Song Name Official Music Video")).status
        == "accepted"
    )


def test_missing_metadata_is_accepted() -> None:
    assert validate_source_candidate(TRACK, source(duration_s=None)).status == "accepted"
    assert validate_source_candidate(TRACK, source(uploader=None, artist=None)).status == "accepted"
    assert (
        validate_source_candidate(TRACK, source(artist=None, uploader=None, duration_s=None)).status
        == "accepted"
    )


def test_wrong_artist_is_rejected_but_ambiguous_title_is_tolerated() -> None:
    assert (
        validate_source_candidate(
            TRACK, source(artist="Another Band", uploader="Another Band")
        ).status
        == "rejected"
    )
    assert validate_source_candidate(TRACK, source(title="Song")).status == "accepted"


def test_validation_does_not_claim_audio_content_is_clean() -> None:
    result = validate_source_candidate(TRACK, source())
    assert result.accepted
    assert "clean" not in " ".join(result.reasons).casefold()
