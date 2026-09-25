"""End-to-end guarantees for playlist order and duplicate preservation."""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from spotm3u.audio import LocalAudioResolver
from spotm3u.exportify import parse_exportify_zip
from spotm3u.m3u import m3u_text
from spotm3u.models import ResolvedTrack, Track
from spotm3u.resolution import TrackResolver


class EmptySearcher:
    def search(self, track):
        return []


def export_zip(*files: tuple[str, str]) -> BytesIO:
    archive = BytesIO()
    with ZipFile(archive, "w") as output:
        for name, content in files:
            output.writestr(name, content)
    archive.seek(0)
    return archive


def entries(text: str) -> list[str]:
    return [line for line in text.splitlines() if line and not line.startswith("#")]


def test_repeated_tracks_keep_positions_and_share_one_file(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    file_a = library / "Alice - Alpha.mp3"
    file_b = library / "Bob - Beta.mp3"
    file_a.write_bytes(b"a")
    file_b.write_bytes(b"b")

    archive = export_zip(
        (
            "Mix.csv",
            "Track URI,Track Name,Album Name,Artist Name(s),Duration (ms)\n"
            "spotify:track:a,Alpha,,Alice,1000\n"
            "spotify:track:b,Beta,,Bob,2000\n"
            "spotify:track:a,Alpha,,Alice,1000\n",
        )
    )
    playlist = parse_exportify_zip(archive)[0]

    resolver = TrackResolver(
        LocalAudioResolver(library), tmp_path / "output", searcher=EmptySearcher()
    )
    report = resolver.resolve_report(playlist.tracks)
    text = m3u_text(report)

    assert [Path(entry).name for entry in entries(text)] == [
        "Alice - Alpha.mp3",
        "Bob - Beta.mp3",
        "Alice - Alpha.mp3",
    ]
    assert entries(text)[0] == entries(text)[2] == str(file_a)
    assert all(result.successful for result in report.results)
    assert report.counts["local"] == 3


def test_unresolved_tracks_are_excluded_but_order_is_preserved(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    file_a = library / "Alice - Alpha.mp3"
    file_b = library / "Bob - Beta.mp3"
    file_a.write_bytes(b"a")
    file_b.write_bytes(b"b")

    archive = export_zip(
        (
            "Mix.csv",
            "Track URI,Track Name,Album Name,Artist Name(s),Duration (ms)\n"
            "spotify:track:a,Alpha,,Alice,1000\n"
            "spotify:track:x,Xray,,Nobody,1500\n"
            "spotify:track:b,Beta,,Bob,2000\n"
            "spotify:track:a,Alpha,,Alice,1000\n",
        )
    )
    playlist = parse_exportify_zip(archive)[0]

    resolver = TrackResolver(
        LocalAudioResolver(library), tmp_path / "output", searcher=EmptySearcher()
    )
    report = resolver.resolve_report(playlist.tracks)
    text = m3u_text(report)

    assert [Path(entry).name for entry in entries(text)] == [
        "Alice - Alpha.mp3",
        "Bob - Beta.mp3",
        "Alice - Alpha.mp3",
    ]
    assert entries(text)[0] == entries(text)[2]
    unresolved = report.results[1]
    assert unresolved.track.title == "Xray"
    assert not unresolved.successful
    assert unresolved.status in {"missing", "ambiguous"}


def test_writer_keeps_duplicate_resolved_entries(tmp_path: Path) -> None:
    file_a = tmp_path / "a.mp3"
    track = Track("Alpha", ["Alice"], duration_ms=1000)
    resolved = ResolvedTrack(track, file_a)

    text = m3u_text([resolved, resolved])

    assert entries(text) == [str(file_a), str(file_a)]
