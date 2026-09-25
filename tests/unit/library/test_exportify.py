from io import BytesIO
from zipfile import ZipFile

import pytest

from spotm3u.exportify import ExportifyParseError, parse_exportify_zip


def export_zip(*files: tuple[str, str]) -> BytesIO:
    archive = BytesIO()
    with ZipFile(archive, "w") as output:
        for name, content in files:
            output.writestr(name, content)
    archive.seek(0)
    return archive


def test_parse_exportify_zip_preserves_unicode_order_and_duplicates() -> None:
    archive = export_zip(
        (
            "夜のプレイリスト.csv",
            "Track URI,Track Name,Album Name,Artist Name(s),Duration (ms)\n"
            'spotify:track:one,"第一曲","专辑","歌手;アーティスト",1000\n'
            'spotify:track:two,"🎵 Song","","Artist",2000\n'
            'spotify:track:one,"第一曲","专辑","歌手;アーティスト",1000\n',
        )
    )

    playlists = parse_exportify_zip(archive)

    assert playlists[0].name == "夜のプレイリスト"
    assert [track.title for track in playlists[0].tracks] == ["第一曲", "🎵 Song", "第一曲"]
    assert playlists[0].tracks[0].artists == ["歌手", "アーティスト"]
    assert playlists[0].tracks[0].spotify_id == "one"


def test_parse_exportify_zip_converts_underscores_to_spaces_in_playlist_name() -> None:
    archive = export_zip(
        (
            "my_spotify_playlist.csv",
            "Track Name,Artist Name(s)\nSong,Artist\n",
        )
    )

    playlists = parse_exportify_zip(archive)

    assert playlists[0].name == "my spotify playlist"


def test_parse_exportify_zip_preserves_collaborating_artists() -> None:
    archive = export_zip(("collab.csv", "Track Name,Artist Name(s)\nSong,Jay Chou;Gary Yang\n"))

    playlists = parse_exportify_zip(archive)

    assert playlists[0].tracks[0].artists == ["Jay Chou", "Gary Yang"]


def test_parse_exportify_zip_rejects_missing_title_column() -> None:
    archive = export_zip(("playlist.csv", "Artist Name(s)\nArtist\n"))

    with pytest.raises(ExportifyParseError, match="track title"):
        parse_exportify_zip(archive)
