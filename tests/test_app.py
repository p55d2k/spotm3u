"""Tests for the Flask application scaffold."""

import re
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from conftest import (
    NoCandidates,
    _job_directory,
    _run_local_match_job,
    _upload_and_select,
    export_zip,
)
from werkzeug.datastructures import MultiDict

from spotm3u import web_jobs
from spotm3u.app import create_app


def test_homepage_renders() -> None:
    client = create_app().test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b"Spotify to M3U Converter" in response.data
    assert b"Exportify" in response.data
    assert b"Export All" in response.data
    assert b"Download the playlist export ZIP" in response.data
    assert b'action="/upload"' in response.data
    assert b'accept=".zip,application/zip"' in response.data


def test_the_import_page_is_an_intentional_empty_state() -> None:
    """A fresh installation says what is empty and what to do about it."""
    client = create_app().test_client()

    page = client.get("/").get_data(as_text=True)

    assert "No playlist imported" in page
    assert "Import an Exportify ZIP to start matching your local music." in page
    # Onboarding stays short enough to read at a glance.
    assert page.count("<li><strong>") == 2
    assert "How it works" in page


def test_the_result_page_explains_a_playlist_with_no_tracks(tmp_path, monkeypatch) -> None:
    """An empty playlist gets an explanation instead of a bare empty page."""
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    archive = BytesIO()
    with ZipFile(archive, "w") as bundle:
        # A playlist file with a header but no rows: a real, empty playlist.
        bundle.writestr("empty.csv", "Track Name,Artist Name(s)\n")
    upload = client.post(
        "/upload",
        data={"file": (BytesIO(archive.getvalue()), "export.zip")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 201
    job_id = _job_directory(tmp_path)
    client.post(f"/playlists/{job_id}/select", data={"playlist_id": "0"})
    client.post(f"/processing/{job_id}/0/start")
    client.application.config["JOB_MANAGER"].get(job_id, "0").wait(timeout=10)

    page = client.get(f"/processing/{job_id}/0/result").get_data(as_text=True)

    assert "No tracks in this playlist" in page
    assert "This playlist is empty, so there is nothing to match or download." in page


def test_the_result_page_explains_when_nothing_matched(tmp_path, monkeypatch) -> None:
    """A playlist where nothing resolved says so, keeping the track reasons."""
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)
    client.post(f"/processing/{job_id}/1/start")
    client.application.config["JOB_MANAGER"].get(job_id, "1").wait(timeout=10)

    page = client.get(f"/processing/{job_id}/1/result").get_data(as_text=True)

    assert "No tracks matched" in page
    assert "Nothing in this playlist was found locally or downloaded" in page
    # The per-track reasons stay listed below it rather than being hidden.
    assert 'id="track-list"' in page


def test_static_stylesheet_is_available() -> None:
    client = create_app().test_client()

    response = client.get("/static/style.css")

    assert response.status_code == 200
    assert b"font-family" in response.data


def test_app_icon_route_serves_the_canonical_artwork() -> None:
    from spotm3u.desktop import webview_icon_path

    client = create_app().test_client()

    response = client.get("/icon.png")

    assert response.status_code == 200
    assert response.mimetype == "image/png"
    # The route serves assets/icon.png itself, so the page never needs a second
    # copy of the artwork to drift out of date.
    assert response.data == webview_icon_path().read_bytes()


def test_app_icon_route_reports_a_missing_icon(monkeypatch) -> None:
    from spotm3u import desktop

    monkeypatch.setattr(desktop, "webview_icon_path", lambda: None)
    client = create_app().test_client()

    response = client.get("/icon.png")

    assert response.status_code == 404


def test_sidebar_brand_shows_the_app_icon_instead_of_a_glyph() -> None:
    client = create_app().test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b'<img class="brand-mark" src="/icon.png"' in response.data
    assert b'<span class="brand-mark"' not in response.data


def test_interface_icons_all_come_from_one_set() -> None:
    app = create_app()
    templates = Path(app.root_path) / "templates"
    css = (Path(app.static_folder) / "style.css").read_text(encoding="utf-8")
    markup = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(templates.glob("*.html"))
    )

    # No emoji, stray Unicode symbol or CSS-drawn glyph stands in for an icon:
    # they all come from the shared Lucide set in _icons.html.
    for glyph in ("\u266a", "\u2191", "\u2713", "\u2190", "\u2600", "\u263e"):
        assert glyph not in markup
        assert glyph not in css
    # The window title bar draws the application icon for the same reason.
    titlebar = (templates / "_titlebar.html").read_text(encoding="utf-8")
    assert '<img class="titlebar-mark"' in titlebar
    assert "url_for('app_icon')" in titlebar

    homepage = app.test_client().get("/")
    assert b'<span class="upload-icon" aria-hidden="true"><svg class="ui-icon"' in homepage.data
    # The live pages build their rows in JS, so they publish the same icons
    # rather than embedding a second copy of the artwork.
    for name in ("processing.html", "batch_processing.html"):
        source = (templates / name).read_text(encoding="utf-8")
        assert "icon_script()" in source
        assert "window.SpotM3U_ICONS.music" in source


def test_button_hierarchy_is_one_shared_set_of_variants() -> None:
    css = (Path(create_app().static_folder) / "style.css").read_text(encoding="utf-8")

    # Four variants and no more: primary is the base style, the other three are
    # modifiers, and every button in the app uses one of them.
    assert ".button,\nbutton {" in css
    for variant in (".button-secondary", ".button-ghost", ".button-danger"):
        assert variant in css
        assert f"{variant}:hover" in css
        assert f"{variant}:active" in css
    # The states that are shared rather than per-variant.
    assert ".button:focus-visible" in css
    assert ".button[disabled]" in css
    assert '.button[aria-busy="true"]' in css
    assert "@keyframes spin" in css
    # A button must not keep its hover feedback while it is pressed or off.
    assert 'button:hover:not([disabled]):not([aria-disabled="true"])' in css
    # No blanket rule styles a button by its type attribute any more; that
    # silently overrode the window controls, the theme toggle and the toast
    # action, which all carry their own design.
    assert 'button[type="button"] {' not in css


def test_controls_report_their_error_and_loading_states() -> None:
    app = create_app()
    css = (Path(app.static_folder) / "style.css").read_text(encoding="utf-8")
    homepage = (Path(app.root_path) / "templates" / "index.html").read_text(encoding="utf-8")

    # A rejected input gets the danger border and the error focus ring.
    assert 'input[aria-invalid="true"] {' in css
    assert "box-shadow: var(--focus-ring-error)" in css
    assert 'uploadInput.setAttribute("aria-invalid", "true")' in homepage
    # And an action in flight keeps its label next to a spinner.
    assert 'uploadButton.setAttribute("aria-busy", busy ? "true" : "false")' in homepage
    # Both import paths go through that one busy state.
    assert "setBusy(true);" in homepage


def test_artwork_styles_support_light_and_dark_themes() -> None:
    import pathlib

    css_folder = create_app().static_folder
    css = (pathlib.Path(css_folder) / "style.css").read_text(encoding="utf-8")

    assert ".track-artwork" in css
    assert "--track" in css
    assert ':root[data-theme="dark"]' in css


def test_fullscreen_macos_drops_the_traffic_light_gap_but_keeps_even_padding() -> None:
    app = create_app()
    css = (Path(app.static_folder) / "style.css").read_text(encoding="utf-8")
    titlebar = (Path(app.root_path) / "templates" / "_titlebar.html").read_text(encoding="utf-8")

    # Full screen hides the traffic lights, so the gap held for them goes away:
    # the brand keeps the same breathing room above and below instead.
    rule = re.search(
        r'body\.has-native-titlebar\.is-fullscreen \.titlebar\[data-platform="mac"\]'
        r" ~ \.app-frame \.sidebar-brand \{(?P<declarations>[^}]*)\}",
        css,
    )
    assert rule is not None
    padding = {
        name.strip(): value.strip().rstrip(";")
        for name, value in (
            line.split(":", 1)
            for line in rule.group("declarations").strip().splitlines()
            if ":" in line
        )
    }
    assert padding["padding-top"] == padding["padding-bottom"]
    # The page can only know about full screen through the window bridge.
    assert 'classList.toggle("is-fullscreen"' in titlebar


class _StubWindowControls:
    """Stands in for the desktop bridge's picked-file handoff."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.takes = 0

    def take_pending_import(self) -> Path | None:
        self.takes += 1
        path, self.path = self.path, None
        return path


def test_upload_picked_imports_the_archive_chosen_in_the_native_dialog(tmp_path) -> None:
    archive = tmp_path / "export.zip"
    archive.write_bytes(export_zip())
    app = create_app({"UPLOAD_ROOT": tmp_path / "uploads"})
    controls = _StubWindowControls(archive)
    app.config["WINDOW_CONTROLS"] = controls
    client = app.test_client()

    response = client.post("/upload/picked")

    assert response.status_code == 201
    payload = response.get_json()
    # The page only needs somewhere to go, so the route answers with the page
    # URL rather than a rendered page it would have to re-request.
    assert payload["url"] == f"/playlists/{payload['job_id']}"
    assert payload["playlists"] == 2
    # The handoff is single-use: the path cannot be imported twice.
    assert controls.take_pending_import() is None
    # The import also opened the session the page navigates into, so the URL
    # the route answers with really is the next page.
    assert client.get(payload["url"]).status_code == 200


def test_upload_picked_needs_a_file_from_the_native_dialog(tmp_path) -> None:
    app = create_app({"UPLOAD_ROOT": tmp_path / "uploads"})
    controls = _StubWindowControls(None)
    app.config["WINDOW_CONTROLS"] = controls

    response = app.test_client().post("/upload/picked")

    assert response.status_code == 400
    assert "Choose the Exportify ZIP file" in response.get_json()["error"]


def test_upload_picked_reports_an_archive_it_cannot_read(tmp_path) -> None:
    broken = tmp_path / "export.zip"
    broken.write_text("this is not a zip", encoding="utf-8")
    app = create_app({"UPLOAD_ROOT": tmp_path / "uploads"})
    app.config["WINDOW_CONTROLS"] = _StubWindowControls(broken)

    response = app.test_client().post("/upload/picked")

    assert response.status_code == 400
    assert "ZIP" in response.get_json()["error"]


def test_the_import_page_uses_the_native_picker_when_the_bridge_is_present() -> None:
    client = create_app().test_client()

    homepage = client.get("/").get_data(as_text=True)

    # The click opens the OS file dialog and the server is asked to take that
    # file; the browser's own control and the drop zone stay as the fallback.
    assert "nativePicker.choose_zip()" in homepage
    assert 'fetch("/upload/picked"' in homepage
    assert 'id="export-file"' in homepage


def test_pages_name_their_actions_the_way_the_application_does() -> None:
    templates = Path(create_app().root_path) / "templates"
    steps = (templates / "_sidebar.html").read_text(encoding="utf-8")
    result = (templates / "result.html").read_text(encoding="utf-8")
    index = (templates / "index.html").read_text(encoding="utf-8")
    playlists = (templates / "playlists.html").read_text(encoding="utf-8")
    missing = (templates / "m3u_missing.html").read_text(encoding="utf-8")

    # The workflow is Import -> Choose playlists -> Convert -> Done, and the
    # controls speak the same words: this is an application window, not a
    # browser tab that uploads and downloads pages.
    assert '("Import", 1,' in steps
    assert '("Convert", 3,' in steps
    assert "Save playlist (M3U)" in result
    assert "Import another ZIP" in result
    assert "Import your export" in index
    assert "Import that ZIP here" in index
    assert "Import a different ZIP" in playlists
    # A browser tab talks about uploading and downloading; the window talks
    # about importing a file and saving a playlist.
    assert "Upload another ZIP" not in result
    assert "Download M3U playlist" not in result
    assert "Cancel and upload a different ZIP" not in playlists
    assert "up to your configured size limit" in index
    assert "up to your configured upload limit" not in index
    assert "or import a different export" in result
    assert "or upload a different export" not in result
    # The warning page saves the playlist; it also still downloads the missing
    # audio files, which is a real download and keeps its own name.
    assert "Save playlist anyway" in missing
    assert "Check before saving" in missing
    assert "Download playlist anyway" not in missing
    assert "Download missing tracks again" in missing


def test_live_pages_take_their_status_wording_from_one_shared_place() -> None:
    templates = Path(create_app().root_path) / "templates"

    icons = (templates / "_status_icons.html").read_text(encoding="utf-8")
    assert "window.SPOTM3U_STATUS_LABEL" in icons
    # Anything without an entry is read as words, not as its identifier.
    assert 'replace(/[-_]+/g, " ")' in icons

    for name in ("processing.html", "batch_processing.html"):
        page = (templates / name).read_text(encoding="utf-8")
        assert "window.SPOTM3U_STATUS_LABEL" in page
        # Each page kept its own map of stage names, which is how a stage the
        # map did not know ("enriching-metadata") reached the screen as a slug.
        assert "stageLabels" not in page


def test_the_missing_files_confirmation_is_a_keyboard_accessible_dialog() -> None:
    app = create_app()
    css = (Path(app.static_folder) / "style.css").read_text(encoding="utf-8")
    template = (Path(app.root_path) / "templates" / "_save_m3u.html").read_text(encoding="utf-8")

    # A native modal element: Tab trapping, Escape to close and focus on open
    # come from <dialog>, and the primary action holds that focus so Enter
    # confirms it. Title and listed files make it self-explanatory.
    assert "<dialog" in template
    assert 'method="dialog"' in template
    assert "autofocus" in template
    assert "showModal()" in template
    assert 'aria-labelledby="m3u-missing-title"' in template
    assert 'id="m3u-missing-list"' in template

    # Sized against the window so it can never be clipped: the group scrolls
    # while the title and the actions stay put.
    assert "max-height: calc(100vh - 2 * var(--space-8))" in css
    assert "max-width: calc(100vw - 2 * var(--space-8))" in css
    assert ".app-dialog::backdrop" in css
    # An author display rule on a closed dialog would keep it on the page.
    assert ".app-dialog[open] {" in css


def test_playlist_selection_persists_state_and_redirects(tmp_path) -> None:
    client = create_app({"UPLOAD_ROOT": tmp_path}).test_client()

    upload = client.post(
        "/upload",
        data={"file": (BytesIO(export_zip()), "export.zip")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 201
    job_id = next(tmp_path.iterdir()).name.removeprefix("job-")
    selection = client.post(
        f"/playlists/{job_id}/select",
        data={"playlist_id": "1"},
    )

    assert selection.status_code == 302
    assert selection.headers["Location"] == f"/processing/{job_id}/1"
    assert (tmp_path / f"job-{job_id}" / "state.json").read_text() == (
        '{"selected_playlist_id": "1"}'
    )
    assert client.get(selection.headers["Location"]).status_code == 200


def test_playlist_selection_shows_clickable_playlist_cards(tmp_path) -> None:
    client = create_app({"UPLOAD_ROOT": tmp_path}).test_client()

    client.post(
        "/upload",
        data={"file": (BytesIO(export_zip()), "export.zip")},
        content_type="multipart/form-data",
    )
    job_id = _job_directory(tmp_path)

    response = client.get(f"/playlists/{job_id}")

    assert response.status_code == 200
    assert b'class="playlist-card"' in response.data
    assert b"one" in response.data
    assert b"two" in response.data
    assert response.data.count(b'name="playlist_id"') == 2
    # The action converts the selected playlists; "download" would describe the
    # track downloads rather than the batch conversion the button starts.
    assert b"Convert selected" in response.data
    assert b"Convert all" in response.data
    assert b"Search by playlist name" in response.data


def test_playlist_selection_rejects_playlist_outside_job(tmp_path) -> None:
    client = create_app({"UPLOAD_ROOT": tmp_path}).test_client()
    client.post(
        "/upload",
        data={"file": (BytesIO(export_zip()), "export.zip")},
        content_type="multipart/form-data",
    )
    job_id = next(tmp_path.iterdir()).name.removeprefix("job-")

    selection = client.post(
        f"/playlists/{job_id}/select",
        data={"playlist_id": "2"},
    )

    assert selection.status_code == 400
    assert b"not available for this upload" in selection.data


def test_batch_selection_processes_all_playlists(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    (music / "Artist - Second.mp3").write_bytes(b"audio")
    monkeypatch.setattr(
        "spotm3u.app.cached_artwork_path",
        lambda _download_dir, _track: music / "artwork.jpg",
    )
    monkeypatch.setattr(
        "spotm3u.web_jobs.cached_artwork_path",
        lambda _download_dir, _track: music / "artwork.jpg",
    )
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    upload = client.post(
        "/upload",
        data={"file": (BytesIO(export_zip()), "export.zip")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 201
    job_id = _job_directory(tmp_path)

    selection = client.post(
        f"/playlists/{job_id}/batch-select",
        data={"playlist_id": ["0", "1"]},
    )
    assert selection.status_code == 302
    assert selection.headers["Location"] == f"/processing/{job_id}/batch"

    started = client.post(f"/processing/{job_id}/batch/start")
    assert started.status_code == 202
    manager = client.application.config["JOB_MANAGER"]
    manager.get(job_id, "0").wait(timeout=10)
    manager.get(job_id, "1").wait(timeout=10)

    status = client.get(f"/processing/{job_id}/batch/status").get_json()
    assert status["status"] == "completed"
    assert status["total"] == 2
    assert len(status["playlists"]) == 2
    assert all(track["artwork"] for playlist in status["playlists"] for track in playlist["tracks"])
    assert client.get(f"/processing/{job_id}/batch/result").status_code == 200
    assert (music / "SpotM3U" / "playlist-0.m3u").is_file()
    assert (music / "SpotM3U" / "playlist-1.m3u").is_file()
    result = client.get(f"/processing/{job_id}/0/result")
    assert result.status_code == 200
    assert b"Local matches" in result.data
    assert f"/processing/{job_id}/batch/result".encode() in result.data


def test_batch_processing_page_renders_full_start_state(tmp_path) -> None:
    client = create_app({"UPLOAD_ROOT": tmp_path}).test_client()
    client.post(
        "/upload",
        data={"file": (BytesIO(export_zip()), "export.zip")},
        content_type="multipart/form-data",
    )
    job_id = _job_directory(tmp_path)
    selection = client.post(
        f"/playlists/{job_id}/batch-select",
        data={"playlist_id": ["0", "1"]},
    )

    assert selection.status_code == 302
    response = client.get(f"/processing/{job_id}/batch")

    assert response.status_code == 200
    assert b"Start batch processing" in response.data
    assert b"undefined / undefined" not in response.data


def test_job_id_is_stored_in_session_and_jobs_are_session_scoped(tmp_path) -> None:
    app = create_app({"UPLOAD_ROOT": tmp_path})
    client = app.test_client()
    other_client = app.test_client()

    client.post(
        "/upload",
        data={"file": (BytesIO(export_zip()), "export.zip")},
        content_type="multipart/form-data",
    )
    job_id = next(tmp_path.iterdir()).name.removeprefix("job-")

    with client.session_transaction() as current_session:
        assert dict(current_session) == {"job_id": job_id}

    assert client.get(f"/playlists/{job_id}").status_code == 200
    assert other_client.get(f"/playlists/{job_id}").status_code == 404


def test_default_download_dir_renames_the_older_folder(tmp_path) -> None:
    """An upgrade finds its downloads under the new name, contents intact."""
    music = tmp_path / "music"
    legacy = music / "SpotM3U-downloads"
    legacy.mkdir(parents=True)
    (legacy / "Artist - Song.mp3").write_bytes(b"audio")
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music})

    resolved = web_jobs._download_dir(app)

    assert resolved == music / "SpotM3U"
    assert (music / "SpotM3U" / "Artist - Song.mp3").read_bytes() == b"audio"
    assert not legacy.exists()


def test_default_download_dir_keeps_both_when_the_new_one_exists(tmp_path) -> None:
    """Two folders are never merged: the new one is used, the older is untouched."""
    music = tmp_path / "music"
    legacy = music / "SpotM3U-downloads"
    legacy.mkdir(parents=True)
    (legacy / "Artist - Old.mp3").write_bytes(b"old")
    (music / "SpotM3U").mkdir()
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music})

    resolved = web_jobs._download_dir(app)

    assert resolved == music / "SpotM3U"
    assert (legacy / "Artist - Old.mp3").is_file()


def test_default_download_dir_keeps_using_a_folder_that_cannot_be_renamed(
    tmp_path, monkeypatch
) -> None:
    """A failed rename must not hide a user's existing downloads."""
    music = tmp_path / "music"
    legacy = music / "SpotM3U-downloads"
    legacy.mkdir(parents=True)

    def deny_rename(self, target):
        raise OSError("cross-device link")

    monkeypatch.setattr(Path, "rename", deny_rename)
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music})

    assert web_jobs._download_dir(app) == legacy
    assert legacy.is_dir()


def test_configured_download_dir_is_never_migrated(tmp_path) -> None:
    """An explicit download_dir names the folder the user chose."""
    music = tmp_path / "music"
    legacy = music / "SpotM3U-downloads"
    legacy.mkdir(parents=True)
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music, "DOWNLOAD_DIR": str(legacy)})

    assert web_jobs._download_dir(app) == legacy
    assert legacy.is_dir()


def test_start_processing_runs_job_and_exposes_state(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.post(f"/processing/{job_id}/1/start")

    assert response.status_code == 202
    state = response.get_json()
    assert state["job_id"] == job_id
    assert state["playlist"]["id"] == "1"
    assert state["playlist"]["total_tracks"] == 1
    assert state["status"] in {"running", "completed"}
    assert state["output_dir"] == str(tmp_path / "music" / "SpotM3U")

    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)
    status = client.get(f"/processing/{job_id}/1/status")
    assert status.status_code == 200
    final = status.get_json()
    assert final["status"] == "completed"
    assert final["completed"] == 1
    assert final["failed"] == 1
    assert final["tracks"][0]["status"] == "failed"
    assert final["m3u_path"] == str(tmp_path / "music" / "SpotM3U" / "playlist.m3u")


def test_processing_job_resolves_local_matches(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)
    final = client.get(f"/processing/{job_id}/1/status").get_json()

    assert final["status"] == "completed"
    assert final["successful"] == 1
    assert final["failed"] == 0
    m3u_path = tmp_path / "music" / "SpotM3U" / "playlist.m3u"
    assert str(music / "Artist - First.mp3") in m3u_path.read_text(encoding="utf-8")


def test_processing_pages_offer_the_fast_mode_toggle(tmp_path) -> None:
    client = create_app({"UPLOAD_ROOT": tmp_path}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    single = client.get(f"/processing/{job_id}/1")

    assert single.status_code == 200
    assert b"Fast mode" in single.data
    assert b'name="fast_mode"' in single.data
    assert b'value="1" checked' not in single.data, "fast mode is off by default"
    # The checkbox must be submitted before its hidden fallback: form fields
    # are read in document order, so a checked box has to come first.
    html = single.data.decode()
    assert html.index('id="fast-mode"') < html.index('name="fast_mode" value="0"')

    client.post(f"/playlists/{job_id}/batch-select", data={"playlist_id": ["0", "1"]})
    batch = client.get(f"/processing/{job_id}/batch")

    assert batch.status_code == 200
    assert b"Fast mode" in batch.data
    assert b'name="fast_mode"' in batch.data


def test_fast_mode_toggle_reflects_the_configuration_default(tmp_path) -> None:
    client = create_app({"UPLOAD_ROOT": tmp_path, "FAST_MODE": True}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.get(f"/processing/{job_id}/1")

    assert response.status_code == 200
    assert b'value="1" checked' in response.data


def test_checked_fast_mode_toggle_wins_over_the_hidden_fallback(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    # Exactly what the page submits with the box checked.
    response = client.post(
        f"/processing/{job_id}/1/start",
        data=MultiDict([("fast_mode", "1"), ("fast_mode", "0")]),
    )

    assert response.status_code == 202
    assert response.get_json()["fast_mode"] is True


def test_start_processing_honours_fast_mode(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())

    def forbidden(*_args, **_kwargs):
        raise AssertionError("fast mode must not enrich metadata")

    monkeypatch.setattr("spotm3u.resolution.enrich_metadata", forbidden)
    built: list[bool] = []
    resolver_class = web_jobs.FastTrackResolver

    class SpyFastResolver(resolver_class):
        def __init__(self, *args, **kwargs):
            built.append(True)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(web_jobs, "FastTrackResolver", SpyFastResolver)
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.post(f"/processing/{job_id}/1/start", data={"fast_mode": "1"})

    assert response.status_code == 202
    assert response.get_json()["fast_mode"] is True
    job = client.application.config["JOB_MANAGER"].get(job_id)
    job.wait(timeout=10)
    final = client.get(f"/processing/{job_id}/1/status").get_json()
    assert final["fast_mode"] is True
    assert final["successful"] == 1
    assert final["failed"] == 0
    assert built == [True], "the fast resolver must be the one that runs"


def test_start_processing_uses_the_configured_fast_mode_and_form_can_override(
    tmp_path, monkeypatch
) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app(
        {"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music, "FAST_MODE": True}
    ).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    configured = client.post(f"/processing/{job_id}/1/start")
    overridden = client.post(f"/processing/{job_id}/1/start", data={"fast_mode": "0"})

    assert configured.status_code == 202
    assert configured.get_json()["fast_mode"] is True
    assert overridden.status_code == 409, "an existing job keeps the mode it started with"


def test_result_page_offers_a_retry_after_the_download_folder_is_emptied(
    tmp_path, monkeypatch
) -> None:
    music = tmp_path / "music"
    music.mkdir()
    # One local file, as in ``test_processing_job_resolves_local_matches``: the
    # selected playlist resolves against it and the job finishes cleanly.
    local_file = music / "Artist - First.mp3"
    local_file.write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)
    client.post(f"/processing/{job_id}/1/start")
    job = client.application.config["JOB_MANAGER"].get(job_id)
    job.wait(timeout=10)

    fresh = client.get(f"/processing/{job_id}/1/result")
    assert b"no longer in the download folder" not in fresh.data
    assert b"unresolved track" not in fresh.data

    local_file.unlink()  # the user deleted everything from SpotM3U

    stale = client.get(f"/processing/{job_id}/1/result")

    assert stale.status_code == 200
    assert b"no longer in the download folder" in stale.data
    assert b"File missing from disk" in stale.data
    assert b"Retry 1 unresolved track" in stale.data

    retried = client.post(f"/processing/{job_id}/1/retry")

    assert retried.status_code == 302, "retry must actually re-resolve the deleted track"
    assert retried.headers["Location"].endswith(f"/processing/{job_id}/1")


def test_start_processing_refuses_second_start(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    first = client.post(f"/processing/{job_id}/1/start")
    job = client.application.config["JOB_MANAGER"].get(job_id)
    job.wait(timeout=5)
    second = client.post(f"/processing/{job_id}/1/start")

    assert first.status_code == 202
    assert second.status_code == 409


def test_status_endpoint_requires_matching_playlist(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.get(f"/processing/{job_id}/0/status")

    assert response.status_code == 404


def test_processing_page_shows_playlist_details(tmp_path) -> None:
    client = create_app({"UPLOAD_ROOT": tmp_path}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.get(f"/processing/{job_id}/1")

    assert response.status_code == 200
    assert b"two" in response.data
    assert b"1 tracks" in response.data
    assert f"/processing/{job_id}/1/start".encode() in response.data
    assert b"Start converting playlist" in response.data
    assert b'action="' + f"/processing/{job_id}/1/start".encode() + b'"' in response.data


def test_download_dir_override_respected(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    download_dir = tmp_path / "custom-downloads"
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app(
        {
            "UPLOAD_ROOT": tmp_path,
            "MUSIC_LIBRARY": music,
            "DOWNLOAD_DIR": str(download_dir),
        }
    ).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.post(f"/processing/{job_id}/1/start")

    assert response.status_code == 202
    assert response.get_json()["output_dir"] == str(download_dir)

    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)
    final = client.get(f"/processing/{job_id}/1/status").get_json()
    assert final["m3u_path"] == str(download_dir / "playlist.m3u")
    assert (download_dir / "playlist.m3u").is_file()


def test_download_m3u_route_returns_playlist(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)

    response = client.get(f"/processing/{job_id}/1/playlist.m3u")

    assert response.status_code == 200
    assert b"#EXTM3U" in response.data
    assert "attachment" in response.headers["Content-Disposition"]
    assert "two.m3u" in response.headers["Content-Disposition"]
    assert response.mimetype == "application/octet-stream"


def test_download_m3u_route_warns_when_referenced_files_are_gone(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    audio = music / "Artist - First.mp3"
    audio.write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)
    # The user deletes the downloaded audio by hand, so the written playlist now
    # points at files that are not on disk any more.
    audio.unlink()

    warning = client.get(f"/processing/{job_id}/1/playlist.m3u")

    assert warning.status_code == 409
    page = warning.get_data(as_text=True)
    assert "no longer in the download folder" in page
    assert "Artist - First.mp3" in page
    assert "/playlist.m3u?confirm=1" in page

    confirmed = client.get(f"/processing/{job_id}/1/playlist.m3u?confirm=1")

    assert confirmed.status_code == 200
    assert b"#EXTM3U" in confirmed.data


def test_download_m3u_route_serves_a_playlist_whose_files_are_all_present(
    tmp_path, monkeypatch
) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)

    response = client.get(f"/processing/{job_id}/1/playlist.m3u")

    assert response.status_code == 200
    assert b"#EXTM3U" in response.data


def test_download_m3u_route_requires_completed_job(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.get(f"/processing/{job_id}/1/playlist.m3u")

    assert response.status_code == 404


def test_result_page_shows_summary_and_reasons(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    (music / "Artist - First.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)

    response = client.get(f"/processing/{job_id}/1/result")

    assert response.status_code == 200
    assert b"Local matches" in response.data
    assert b"Successfully resolved" in response.data
    assert b"Total tracks: 1" in response.data
    assert f"/processing/{job_id}/1/playlist.m3u".encode() in response.data
    assert f"/playlists/{job_id}".encode() in response.data
    assert b"Convert another playlist" in response.data


def test_result_page_offers_retry_for_unresolved_tracks(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music})
    client = app.test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    app.config["JOB_MANAGER"].get(job_id).wait(timeout=10)

    result = client.get(f"/processing/{job_id}/1/result")

    assert b"Retry 1 unresolved track" in result.data
    assert f"/processing/{job_id}/1/retry".encode() in result.data

    retry = client.post(f"/processing/{job_id}/1/retry")

    assert retry.status_code == 302
    assert retry.headers["Location"] == f"/processing/{job_id}/1"
    job = app.config["JOB_MANAGER"].get(job_id)
    job.wait(timeout=10)
    assert job.status == "completed"
    assert (music / "SpotM3U" / "playlist.m3u").is_file()


class AmbiguousResolver:
    """Resolver that reports every track as ambiguous, without any network work."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def resolve(self, track, *, stage_callback=None):
        from spotm3u.resolution import TrackResolution

        if stage_callback is not None:
            stage_callback("searching")
        return TrackResolution(track, "ambiguous", reasons=("multiple candidates",))


def test_result_page_filters_failed_and_ambiguous_tracks(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.TrackResolver", AmbiguousResolver)
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music})
    client = app.test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    app.config["JOB_MANAGER"].get(job_id).wait(timeout=10)

    response = client.get(f"/processing/{job_id}/1/result")

    assert b'data-track-filter="all"' in response.data
    assert b'data-track-filter="failed"' in response.data
    assert b"Failed or ambiguous" in response.data
    assert b'id="track-list"' in response.data
    assert b'id="no-tracks-match"' in response.data
    # One ambiguous track is listed, and its count matches the summary stat.
    assert b'<span class="filter-count">1</span>' in response.data
    assert b'class="track-ambiguous"' in response.data
    assert b"<dt>Ambiguous</dt><dd>1</dd>" in response.data


def test_result_page_filter_ignores_tracks_that_only_failed_to_match(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music})
    client = app.test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    app.config["JOB_MANAGER"].get(job_id).wait(timeout=10)

    response = client.get(f"/processing/{job_id}/1/result")

    # A missing track is retryable, but it is not a failed or ambiguous result.
    assert b"Retry 1 unresolved track" in response.data
    assert b'class="track-filters"' not in response.data
    assert b"<dt>Missing</dt><dd>1</dd>" in response.data


def test_result_page_hides_filter_and_retry_when_every_track_resolved(
    tmp_path, monkeypatch
) -> None:
    music = tmp_path / "music"
    music.mkdir()
    # Playlist 1 is "two", whose only track is "Second" by "Artist".
    (music / "Artist - Second.mp3").write_bytes(b"audio")
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music})
    client = app.test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    job = app.config["JOB_MANAGER"].get(job_id)
    job.wait(timeout=10)

    response = client.get(f"/processing/{job_id}/1/result")

    assert job.status == "completed"
    assert b"Retry" not in response.data
    assert b'class="track-filters"' not in response.data
    assert b'class="track-complete"' in response.data


def test_retry_route_rejects_unknown_job(tmp_path) -> None:
    music = tmp_path / "music"
    music.mkdir()
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    response = client.post(f"/processing/{job_id}/1/retry")

    assert response.status_code == 404


def test_result_page_redirects_while_job_running(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()
    monkeypatch.setattr("spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: NoCandidates())
    app = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music})
    client = app.test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    class BlockingResolver:
        def resolve(self, track, *, stage_callback=None):
            import threading

            threading.Event().wait(timeout=30)
            raise RuntimeError("unreachable")

    from spotm3u.jobs import ProcessingJob
    from spotm3u.models import Track

    playlist = app.config["JOB_MANAGER"]
    job = ProcessingJob(
        job_id=job_id,
        playlist_id="1",
        playlist_name="two",
        tracks=[Track("First", ["Artist"], duration_ms=200_000)],
        output_dir=music / "SpotM3U",
        resolver_factory=lambda: BlockingResolver(),
    )
    playlist.submit(job)
    job.start()

    response = client.get(f"/processing/{job_id}/1/result")

    assert response.status_code == 302
    assert response.headers["Location"] == f"/processing/{job_id}/1"
    job.wait(timeout=1)


def test_result_page_exposes_rejected_reasons(tmp_path, monkeypatch) -> None:
    music = tmp_path / "music"
    music.mkdir()

    class RejectingCandidates:
        def search(self, track):
            return ()

    monkeypatch.setattr(
        "spotm3u.web_jobs.OnlineSourceSearcher", lambda **kwargs: RejectingCandidates()
    )
    client = create_app({"UPLOAD_ROOT": tmp_path, "MUSIC_LIBRARY": music}).test_client()
    job_id, _selection = _upload_and_select(tmp_path, client)

    client.post(f"/processing/{job_id}/1/start")
    client.application.config["JOB_MANAGER"].get(job_id).wait(timeout=10)

    response = client.get(f"/processing/{job_id}/1/result")

    assert response.status_code == 200
    assert b"no online source candidates" in response.data


def test_result_page_labels_the_embedded_lyrics_form(tmp_path, monkeypatch) -> None:
    """The lyrics badge follows the file's tags, and needs an embedded frame."""
    import mutagen.id3 as mutagen_id3

    client, job, _download_dir = _run_local_match_job(tmp_path, monkeypatch)
    path = Path(job.as_dict()["tracks"][0]["local_path"])
    result_url = f"/processing/{job.job_id}/1/result"

    # The lyrics library is stubbed to find nothing, so no lyrics were written.
    response = client.get(result_url)
    assert response.status_code == 200
    assert b"lyrics-badge" not in response.data

    plain = "Today is gonna be the day\nThat they're gonna throw it back to you"
    synced = f"[00:06.21] {plain.splitlines()[0]}\n[00:11.00] {plain.splitlines()[1]}"
    for text, badge in ((plain, b"Plain lyrics"), (synced, b"Synced lyrics")):
        tags = mutagen_id3.ID3(str(path))
        tags.delall("USLT")
        tags["USLT"] = mutagen_id3.USLT(encoding=3, lang="eng", desc="", text=text)
        tags.save(str(path))

        response = client.get(result_url)

        assert badge in response.data


def test_artwork_route_returns_cached_image(tmp_path, monkeypatch) -> None:
    from spotm3u import artwork, artwork_cache

    client, job, download_dir = _run_local_match_job(tmp_path, monkeypatch)
    track = job.tracks[0]
    artwork_path = artwork_cache._cached_artwork_path(
        artwork_cache._cache_dir(download_dir),
        artwork_cache._cache_key(
            artwork.artwork_artist(track) or "", track.album or "", track.title
        ),
    )
    artwork_path.write_bytes(b"cached-artwork-bytes")

    response = client.get(f"/processing/{job.job_id}/1/artwork/0")

    assert response.status_code == 200
    assert response.data == b"cached-artwork-bytes"
    assert response.headers["Content-Type"] == "image/jpeg"


def test_artwork_route_404_when_unavailable(tmp_path, monkeypatch) -> None:
    client, job, _download_dir = _run_local_match_job(tmp_path, monkeypatch)

    response = client.get(f"/processing/{job.job_id}/1/artwork/0")

    assert response.status_code == 404


def test_artwork_route_404_for_unknown_index(tmp_path, monkeypatch) -> None:
    client, job, _download_dir = _run_local_match_job(tmp_path, monkeypatch)

    response = client.get(f"/processing/{job.job_id}/1/artwork/5")

    assert response.status_code == 404


def test_result_page_shows_artwork_image_when_available(tmp_path, monkeypatch) -> None:
    from spotm3u import artwork, artwork_cache

    client, job, download_dir = _run_local_match_job(tmp_path, monkeypatch)
    track = job.tracks[0]
    artwork_path = artwork_cache._cached_artwork_path(
        artwork_cache._cache_dir(download_dir),
        artwork_cache._cache_key(
            artwork.artwork_artist(track) or "", track.album or "", track.title
        ),
    )
    artwork_path.write_bytes(b"cached-artwork-bytes")

    response = client.get(f"/processing/{job.job_id}/1/result")

    assert response.status_code == 200
    assert b'class="track-art-img"' in response.data
    assert f"/processing/{job.job_id}/1/artwork/0".encode() in response.data


def test_artwork_is_hidden_for_a_download_that_was_deleted(tmp_path, monkeypatch) -> None:
    from spotm3u import artwork, artwork_cache

    client, job, download_dir = _run_local_match_job(tmp_path, monkeypatch)
    track = job.tracks[0]
    artwork_path = artwork_cache._cached_artwork_path(
        artwork_cache._cache_dir(download_dir),
        artwork_cache._cache_key(
            artwork.artwork_artist(track) or "", track.album or "", track.title
        ),
    )
    artwork_path.write_bytes(b"cached-artwork-bytes")

    resolved_path = Path(job.as_dict()["tracks"][0]["local_path"])
    resolved_path.unlink()  # the user deleted the download by hand

    result = client.get(f"/processing/{job.job_id}/1/result")

    assert result.status_code == 200
    assert b'class="track-art-img"' not in result.data
    assert f"/processing/{job.job_id}/1/artwork/0".encode() not in result.data

    # The image itself is not served either, so no surface claims the download
    # is still there. Hiding is not deleting: the retry reclaims the cache.
    assert client.get(f"/processing/{job.job_id}/1/artwork/0").status_code == 404
    assert artwork_path.is_file()


def test_status_reports_no_artwork_for_a_deleted_download(tmp_path, monkeypatch) -> None:
    from spotm3u import artwork, artwork_cache

    client, job, download_dir = _run_local_match_job(tmp_path, monkeypatch)
    track = job.tracks[0]
    artwork_cache._cached_artwork_path(
        artwork_cache._cache_dir(download_dir),
        artwork_cache._cache_key(
            artwork.artwork_artist(track) or "", track.album or "", track.title
        ),
    ).write_bytes(b"cached-artwork-bytes")

    assert (
        client.get(f"/processing/{job.job_id}/1/status").get_json()["tracks"][0]["artwork"] is True
    )

    Path(job.as_dict()["tracks"][0]["local_path"]).unlink()

    state = client.get(f"/processing/{job.job_id}/1/status").get_json()

    assert state["tracks"][0]["file_missing"] is True
    assert state["tracks"][0]["artwork"] is False


def test_result_page_shows_placeholder_without_artwork(tmp_path, monkeypatch) -> None:
    client, job, _download_dir = _run_local_match_job(tmp_path, monkeypatch)

    response = client.get(f"/processing/{job.job_id}/1/result")

    assert response.status_code == 200
    assert b'class="track-art-img"' not in response.data
    assert b'class="track-artwork"' in response.data
