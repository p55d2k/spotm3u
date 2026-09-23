"""Tests for the shared UI system: icons, buttons, surfaces and shell chrome.

These lock in the application's one design system (one icon set, one button
hierarchy, border-based flat surfaces) and the wording the shell uses, so the
rules are asserted in one place instead of across the page-behaviour tests in
``test_app.py``. Layout is measured in a real browser in ``test_ui_layout.py``.
"""

import re
from pathlib import Path

from spotm3u.app import create_app


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


def test_surfaces_stay_flat_square_and_unshaded() -> None:
    """The anti-vibe-code rules: no gradients, small radii, borders not shadows."""
    css = (Path(create_app().static_folder) / "style.css").read_text(encoding="utf-8")

    # No gradient anywhere: this is a desktop utility, not a landing page.
    assert "gradient(" not in css
    # An ordinary surface separates with a border and a surface tone; only an
    # overlay (dialog, toast, tooltip) casts a shadow.
    assert "--shadow-panel" not in css
    assert "--shadow-raised" not in css
    assert "box-shadow: var(--shadow-popover)" in css
    # The radius scale stays small, and 999px is reserved for something that is
    # genuinely round (a status dot, a spinner) rather than a pill-shaped label.
    radius = dict(re.findall(r"(--radius-[\w-]+): ([\d.]+rem);", css))
    assert radius["--radius-xl"] == "0.5rem"
    assert radius["--radius-l"] == "0.5rem"
    assert "--radius-2xl" not in css
    assert "border-radius: 999px" not in css
    assert "border-radius: 9999px" not in css
    # A card never floats off the baseline when it is pointed at.
    assert "translateY(-1px)" not in css


def test_non_primary_buttons_beat_the_primary_hover() -> None:
    """Window controls and the toast action are not the primary action.

    The base ``button`` rule paints any bare <button> with the accent, and its
    ``button:hover`` match is more specific than a single class, so a component
    hover has to be qualified with its element - exactly as the secondary and
    ghost variants are. Without that the minimize, maximize and close controls
    (and the toast action) repaint with the primary accent on hover.
    """
    css = (Path(create_app().static_folder) / "style.css").read_text(encoding="utf-8")

    for selector in (
        'button.titlebar-button:hover:not([disabled]):not([aria-disabled="true"])',
        'button.titlebar-button:active:not([disabled]):not([aria-disabled="true"])',
        'button.titlebar-button-close:hover:not([disabled]):not([aria-disabled="true"])',
        'button.toast-action:hover:not([disabled]):not([aria-disabled="true"])',
        'button.toast-action:active:not([disabled]):not([aria-disabled="true"])',
    ):
        assert selector in css
    # The unqualified forms would lose to the base hover, so they must be gone.
    assert ".titlebar-button:hover {" not in css
    assert ".toast-action:hover {" not in css


def test_artwork_styles_support_light_and_dark_themes() -> None:
    css = (Path(create_app().static_folder) / "style.css").read_text(encoding="utf-8")

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
