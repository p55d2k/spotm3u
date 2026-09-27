"""Tests for the developer inventory and clear routes."""

from pathlib import Path

from spotm3u.app import create_app
from spotm3u.maintenance import CONFIRM_PHRASE


def _client(tmp_path: Path, **config):
    """An API client whose download folder and upload root stay in ``tmp_path``."""
    music = tmp_path / "music"
    music.mkdir(exist_ok=True)
    return create_app(
        {"UPLOAD_ROOT": tmp_path / "uploads", "MUSIC_LIBRARY": music, **config}
    ).test_client()


def _populated(client) -> Path:
    """One of everything the clear knows about, inside the client's download folder."""
    download = Path(client.application.config["MUSIC_LIBRARY"]) / "SpotM3U"
    (download / "artwork_cache").mkdir(parents=True)
    (download / "lyrics_cache").mkdir()
    (download / "Artist - Song.mp3").write_bytes(b"x" * 12)
    (download / "playlist-1.m3u").write_text("song", encoding="utf-8")
    (download / ".spotm3u-cache.json").write_text("{}", encoding="utf-8")
    (download / "artwork_cache" / "cover.jpg").write_bytes(b"a" * 6)
    (download / "lyrics_cache" / "song.lrc").write_text("[00:00]x", encoding="utf-8")
    return download


def test_inventory_reports_every_item_and_the_confirm_phrase(tmp_path) -> None:
    client = _client(tmp_path)
    _populated(client)

    response = client.get("/api/developer/inventory")

    assert response.status_code == 200
    body = response.get_json()
    assert body["confirm_phrase"] == CONFIRM_PHRASE
    assert body["download_dir"].endswith("SpotM3U")
    assert {item["name"]: item["files"] for item in body["items"]} == {
        "songs": 1,
        "playlists": 1,
        "manifest": 1,
        "artwork": 1,
        "lyrics": 1,
        "uploads": 0,
    }


def test_inventory_of_an_untouched_library_is_all_empty(tmp_path) -> None:
    client = _client(tmp_path)

    body = client.get("/api/developer/inventory").get_json()

    assert all(item["files"] == 0 for item in body["items"])


def test_clear_deletes_the_selected_items_and_reports_them(tmp_path) -> None:
    client = _client(tmp_path)
    download = _populated(client)

    response = client.post(
        "/api/developer/clear", json={"items": ["songs", "lyrics"], "confirm": CONFIRM_PHRASE}
    )

    assert response.status_code == 200
    assert response.get_json()["removed"] == {"songs": 1, "lyrics": 1}
    assert not (download / "Artist - Song.mp3").exists()
    assert not (download / "lyrics_cache").exists()
    assert (download / "playlist-1.m3u").is_file()
    assert (download / "artwork_cache" / "cover.jpg").is_file()
    assert (client.get("/api/developer/inventory").get_json()["items"][0]["files"]) == 0


def test_clear_never_reaches_outside_the_download_folder(tmp_path) -> None:
    client = _client(tmp_path)
    _populated(client)
    precious = Path(client.application.config["MUSIC_LIBRARY"]) / "Precious.mp3"
    precious.write_bytes(b"p")

    client.post(
        "/api/developer/clear",
        json={
            "items": ["songs", "playlists", "manifest", "artwork", "lyrics"],
            "confirm": CONFIRM_PHRASE,
        },
    )

    assert precious.is_file()


def test_clear_requires_the_confirmation_phrase(tmp_path) -> None:
    client = _client(tmp_path)
    download = _populated(client)

    for body in ({"items": ["songs"]}, {"items": ["songs"], "confirm": "yes"}, {}):
        response = client.post("/api/developer/clear", json=body)

        assert response.status_code == 400
        assert response.get_json()["code"] == "clear_invalid"

    assert (download / "Artist - Song.mp3").is_file()


def test_clear_refuses_an_empty_or_unknown_selection(tmp_path) -> None:
    client = _client(tmp_path)
    download = _populated(client)

    empty = client.post("/api/developer/clear", json={"items": [], "confirm": CONFIRM_PHRASE})
    unknown = client.post(
        "/api/developer/clear", json={"items": ["songs", "everything"], "confirm": CONFIRM_PHRASE}
    )

    assert (empty.status_code, empty.get_json()["code"]) == (400, "clear_invalid")
    assert (unknown.status_code, unknown.get_json()["code"]) == (400, "clear_invalid")
    assert "everything" in unknown.get_json()["error"]
    assert (download / "Artist - Song.mp3").is_file()


def test_clear_waits_while_a_conversion_is_running(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path)
    download = _populated(client)
    manager = client.application.config["JOB_MANAGER"]
    monkeypatch.setattr(manager, "active_job_ids", lambda: {"job-abc"})

    response = client.post(
        "/api/developer/clear", json={"items": ["songs"], "confirm": CONFIRM_PHRASE}
    )

    assert (response.status_code, response.get_json()["code"]) == (409, "clear_refused")
    assert (download / "Artist - Song.mp3").is_file()


def test_clear_refuses_a_download_folder_that_is_the_music_library(tmp_path) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Precious.mp3").write_bytes(b"p")
    client = _client(tmp_path, DOWNLOAD_DIR=str(music))

    response = client.post(
        "/api/developer/clear", json={"items": ["songs"], "confirm": CONFIRM_PHRASE}
    )

    assert (response.status_code, response.get_json()["code"]) == (409, "clear_refused")
    assert (music / "Precious.mp3").is_file()
