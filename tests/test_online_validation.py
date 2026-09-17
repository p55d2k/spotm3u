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
        "Song Name Official Music Video",
        "Song Name Movie Scene",
        "Song Name Karaoke",
    ):
        assert validate_source_candidate(TRACK, source(title=title)).status == "rejected"


def test_missing_metadata_is_uncertain() -> None:
    assert validate_source_candidate(TRACK, source(duration_s=None)).status == "uncertain"
    assert validate_source_candidate(TRACK, source(uploader=None, artist=None)).status == "uncertain"


def test_wrong_artist_and_ambiguous_title_are_not_accepted() -> None:
    assert validate_source_candidate(TRACK, source(artist="Other Artist", uploader="Other Artist")).status == "rejected"
    assert validate_source_candidate(TRACK, source(title="Song")).status == "uncertain"


def test_validation_does_not_claim_audio_content_is_clean() -> None:
    result = validate_source_candidate(TRACK, source())
    assert result.accepted
    assert "clean" not in " ".join(result.reasons).casefold()
