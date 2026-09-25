from pathlib import Path

from spotm3u.models import Track
from spotm3u.normalization import filename_keys, normalize, normalize_artists, track_key


def test_normalize_handles_unicode_case_punctuation_and_whitespace() -> None:
    assert normalize("  Beyoncé — Déjà Vu! ") == "beyonce deja vu"
    assert normalize("Артист_日本語") == "артист 日本語"


def test_normalize_artists_accepts_common_collaboration_formats() -> None:
    assert normalize_artists("Artist feat. Guest") == "artist guest"
    assert normalize_artists(["Artist", "Guest"]) == "artist guest"
    assert normalize_artists("Artist & Guest") == "artist guest"


def test_filename_keys_cover_numbering_and_artist_title_order() -> None:
    keys = filename_keys(Path("01 - Artist - Song (Official Audio).mp3"))

    assert "artist song" in keys
    assert "song artist" in keys


def test_track_key_does_not_modify_original_metadata() -> None:
    track = Track(title="Déjà Vu", artists=["Beyoncé"], album="Album")

    assert track_key(track.title, track.artists) == "deja vu beyonce"
    assert track.title == "Déjà Vu"
    assert track.artists == ["Beyoncé"]
