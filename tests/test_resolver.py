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


def test_resolver_typo_fuzzy_match_via_pruned_index(tmp_path: Path) -> None:
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    expected = music_dir / "Artist - Wonderwal.mp3"
    expected.write_bytes(b"audio")

    result = LocalAudioResolver(music_dir).resolve(Track(title="Wonderwall", artists=["Artist"]))

    assert result.status == "matched"
    assert result.resolved is not None
    assert result.resolved.local_path == expected


def test_resolver_matches_single_char_cjk_title_inside_longer_word(tmp_path: Path) -> None:
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    expected = music_dir / "薛之谦 - 愿望.mp3"
    expected.write_bytes(b"audio")

    result = LocalAudioResolver(music_dir).resolve(Track(title="愿", artists=["薛之谦"]))

    assert result.status == "matched"
    assert result.resolved is not None
    assert result.resolved.local_path == expected


def test_resolver_parallel_resolve_all_preserves_order(tmp_path: Path) -> None:
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    paths = [
        music_dir / "Artist - Alpha.mp3",
        music_dir / "Artist - Beta.mp3",
        music_dir / "Artist - Gamma.flac",
    ]
    for path in paths:
        path.write_bytes(b"audio")

    tracks = [
        Track(title="Alpha", artists=["Artist"]),
        Track(title="Missing Track", artists=["Nope"]),
        Track(title="Beta", artists=["Artist"]),
        Track(title="Gamma", artists=["Artist"]),
        Track(title="Alpha", artists=["Artist"]),
    ]

    resolver = LocalAudioResolver(music_dir)
    parallel = resolver.resolve_all(tracks, max_workers=4)
    sequential = resolver.resolve_all(tracks)

    assert [result.status for result in parallel] == [result.status for result in sequential]
    assert [result.resolved.local_path if result.resolved else None for result in parallel] == [
        result.resolved.local_path if result.resolved else None for result in sequential
    ]
