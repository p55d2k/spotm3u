"""Tests for the project data model foundation."""

from pathlib import Path

from spotm3u.models import ResolvedTrack, Track


def test_resolved_track_keeps_track_metadata_and_path() -> None:
    track = Track(title="Song", artists=["Artist"])

    resolved = ResolvedTrack(track=track, local_path=Path("music/song.mp3"))

    assert resolved.track == track
    assert resolved.local_path == Path("music/song.mp3")
