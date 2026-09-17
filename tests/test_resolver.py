from pathlib import Path

from spotm3u.audio import LocalAudioResolver
from spotm3u.models import Track


def test_resolver_matches_exact_normalized_filename(tmp_path: Path) -> None:
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    expected = music_dir / "Artist - Song Name.mp3"
    expected.write_bytes(b"audio")

    result = LocalAudioResolver(music_dir).resolve(Track(title="Song Name", artists=["Artist"]))

    assert result.status == "matched"
    assert result.resolved is not None
    assert result.resolved.local_path == expected


def test_resolver_rejects_ambiguous_matches(tmp_path: Path) -> None:
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "Artist - Song Name.mp3").write_bytes(b"audio")
    (music_dir / "Artist - Song Name (Live).mp3").write_bytes(b"audio")

    result = LocalAudioResolver(music_dir).resolve(Track(title="Song Name", artists=["Artist"]))

    assert result.status == "ambiguous"
    assert result.resolved is None
    assert len(result.candidates) == 2


def test_resolver_uses_fuzzy_matching_when_close(tmp_path: Path) -> None:
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    expected = music_dir / "Artist - Son Name.mp3"
    expected.write_bytes(b"audio")

    result = LocalAudioResolver(music_dir).resolve(Track(title="Song Name", artists=["Artist"]))

    assert result.status == "matched"
    assert result.resolved is not None
    assert result.resolved.local_path == expected
