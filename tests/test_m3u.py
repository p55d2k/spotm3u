from pathlib import Path

from spotm3u.m3u import check_playlist, m3u_entries, m3u_text, write_m3u
from spotm3u.models import ResolvedTrack, Track
from spotm3u.resolution import ResolutionReport, TrackResolution


def _track(title: str = "Song", artist: str = "Artist") -> Track:
    return Track(title, [artist], duration_ms=200_000)


def _resolved(path: Path, title: str = "Song") -> ResolvedTrack:
    return ResolvedTrack(_track(title), path)


def _unresolved(status: str) -> TrackResolution:
    return TrackResolution(_track(), status)


def test_preserves_order_and_duplicates(tmp_path: Path) -> None:
    file_a = tmp_path / "a.mp3"
    file_b = tmp_path / "b.mp3"
    tracks = [_resolved(file_a, "A"), _resolved(file_b, "B"), _resolved(file_a, "A")]

    text = m3u_text(tracks)

    assert text.count(str(file_a)) == 2
    assert text.count(str(file_b)) == 1
    assert text.index(str(file_a)) < text.index(str(file_b)) < text.rindex(str(file_a))


def test_never_includes_unresolved_tracks(tmp_path: Path) -> None:
    file_a = tmp_path / "a.mp3"
    tracks = [
        _resolved(file_a, "A"),
        _unresolved("missing"),
        _unresolved("ambiguous"),
        _unresolved("rejected"),
        _unresolved("failed"),
        _unresolved("uncertain"),
        _resolved(file_a, "A"),
    ]

    text = m3u_text(tracks)

    assert text.count(str(file_a)) == 2
    assert "#EXTINF" in text
    assert "Song" not in text


def test_handles_resolution_report(tmp_path: Path) -> None:
    file_a = tmp_path / "a.mp3"
    report = ResolutionReport(
        tuple(
            [
                TrackResolution(_track("A"), "local", resolved=_resolved(file_a, "A")),
                _unresolved("missing"),
            ]
        )
    )

    text = m3u_text(report)

    assert str(file_a) in text
    assert "missing" not in text


def test_write_m3u_writes_file_and_returns_entry_count(tmp_path: Path) -> None:
    file_a = tmp_path / "a.mp3"
    output = tmp_path / "nested" / "playlist.m3u"
    tracks = [_resolved(file_a), _unresolved("missing")]

    count = write_m3u(output, tracks)

    assert count == 1
    assert output.read_text(encoding="utf-8") == m3u_text(tracks)


def test_extended_format_contains_inf_lines(tmp_path: Path) -> None:
    file_a = tmp_path / "a.mp3"
    text = m3u_text([_resolved(file_a, "A")], extended=True)

    assert text.startswith("#EXTM3U\n")
    assert "#EXTINF:200,Artist - A\n" in text


def test_plain_format_has_no_extinf(tmp_path: Path) -> None:
    file_a = tmp_path / "a.mp3"
    text = m3u_text([_resolved(file_a)], extended=False)

    assert text == str(file_a) + "\n"


def test_relative_to_uses_relative_paths(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    file_a = library / "a.mp3"
    base = tmp_path / "playlists"

    text = m3u_text([_resolved(file_a)], relative_to=base)

    assert str(file_a) not in text
    assert "a.mp3" in text


def test_relative_paths_use_forward_slashes(tmp_path: Path) -> None:
    library = tmp_path / "library"
    file_a = library / "albums" / "a.mp3"
    file_a.parent.mkdir(parents=True)

    text = m3u_text([_resolved(file_a)], relative_to=library)

    assert "albums/a.mp3" in text
    assert "\\" not in text


def test_no_source_urls_embedded(tmp_path: Path) -> None:
    file_a = tmp_path / "a.mp3"
    resolved = ResolvedTrack(
        _track(),
        file_a,
        resolution_method="downloaded",
        source_url="https://example.com/source",
        status="downloaded",
    )

    text = m3u_text([resolved])

    assert "https://example.com/source" not in text
    assert str(file_a) in text


def test_check_playlist_reports_files_missing_from_disk(tmp_path: Path) -> None:
    present = tmp_path / "present.mp3"
    present.write_bytes(b"audio")
    playlist = tmp_path / "playlist.m3u"
    playlist.write_text(
        "\n".join(["#EXTM3U", str(present), str(tmp_path / "gone.mp3"), ""]),
        encoding="utf-8",
    )

    check = check_playlist(playlist)

    assert check.total == 2
    assert check.complete is False
    assert check.missing_count == 1
    assert check.missing == (tmp_path / "gone.mp3",)
    assert check.missing_names() == ("gone.mp3",)


def test_check_playlist_resolves_relative_entries_against_the_playlist(tmp_path: Path) -> None:
    downloads = tmp_path / "SpotM3U-downloads"
    downloads.mkdir()
    audio = downloads / "Artist - Song.mp3"
    audio.write_bytes(b"audio")
    playlist = downloads / "playlist.m3u"
    playlist.write_text("#EXTM3U\nArtist - Song.mp3\n", encoding="utf-8")

    assert check_playlist(playlist).complete is True

    audio.unlink()

    assert check_playlist(playlist).missing == (audio,)


def test_check_playlist_ignores_comments_and_blank_lines(tmp_path: Path) -> None:
    playlist = tmp_path / "playlist.m3u"
    playlist.write_text("#EXTM3U\n\n# a comment\n", encoding="utf-8")

    check = check_playlist(playlist)

    assert m3u_entries(playlist) == ()
    assert check.total == 0
    assert check.complete is True


def test_check_playlist_treats_an_unreadable_playlist_as_empty(tmp_path: Path) -> None:
    check = check_playlist(tmp_path / "does-not-exist.m3u")

    assert check.entries == ()
    assert check.complete is True


def test_missing_names_are_unique_and_capped(tmp_path: Path) -> None:
    playlist = tmp_path / "playlist.m3u"
    entries = [f"gone-{index}.mp3" for index in range(15)] + ["gone-0.mp3"]
    playlist.write_text("#EXTM3U\n" + "\n".join(entries) + "\n", encoding="utf-8")

    check = check_playlist(playlist)

    assert check.missing_count == 16  # duplicates count per entry
    names = check.missing_names()
    assert len(names) == 10
    assert names[0] == "gone-0.mp3"
    assert len(set(names)) == 10


def test_written_playlist_is_checked_by_its_own_output(tmp_path: Path) -> None:
    audio = tmp_path / "SpotM3U-downloads" / "Artist - Song.mp3"
    audio.parent.mkdir()
    audio.write_bytes(b"audio")
    playlist = audio.parent / "playlist.m3u"
    write_m3u(playlist, [_resolved(audio)], relative_to=audio.parent)

    assert check_playlist(playlist).complete is True

    audio.unlink()
    missing = check_playlist(playlist)

    assert missing.missing_count == 1
    assert missing.missing_names() == ("Artist - Song.mp3",)
