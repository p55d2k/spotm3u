"""Tests for the UI preference endpoints.

The theme is a user choice the application has to remember itself: the desktop
window keeps no WebView storage, so a page-local copy is gone by the next
launch. These tests pin the contract the frontend relies on - read back what
was stored, refuse what cannot be stored, and never fail the request over it.
"""

from __future__ import annotations

import pytest
from flask import Flask

from spotm3u import preferences
from spotm3u.api import register_api


@pytest.fixture(autouse=True)
def _state_dir(monkeypatch, tmp_path):
    """Store this test's preferences in a temporary state directory."""
    monkeypatch.setenv(preferences.STATE_DIR_ENV, str(tmp_path / "state"))


@pytest.fixture
def client():
    app = Flask(__name__)
    register_api(app)
    return app.test_client()


def test_preferences_start_empty(client) -> None:
    response = client.get("/api/preferences")

    assert response.status_code == 200
    assert response.get_json() == {}


def test_a_theme_can_be_stored_and_read_back(client) -> None:
    stored = client.put("/api/preferences", json={"theme": "dark"})

    assert stored.status_code == 200
    assert stored.get_json() == {"theme": "dark"}
    assert client.get("/api/preferences").get_json() == {"theme": "dark"}


def test_a_theme_can_be_changed(client) -> None:
    client.put("/api/preferences", json={"theme": "dark"})

    changed = client.put("/api/preferences", json={"theme": "light"})

    assert changed.get_json() == {"theme": "light"}
    assert client.get("/api/preferences").get_json() == {"theme": "light"}


@pytest.mark.parametrize("theme", ["neon", "system", "LIGHT", "", 3, None])
def test_an_unsupported_theme_is_refused(client, theme) -> None:
    response = client.put("/api/preferences", json={"theme": theme})

    assert response.status_code == 400
    body = response.get_json()
    assert body["code"] == "preference_invalid"
    assert "light" in body["error"] and "dark" in body["error"]
    assert client.get("/api/preferences").get_json() == {}


def test_an_unknown_preference_is_refused(client) -> None:
    response = client.put("/api/preferences", json={"colour": "red"})

    assert response.status_code == 400
    assert response.get_json()["code"] == "preference_invalid"
    assert "colour" in response.get_json()["error"]


@pytest.mark.parametrize("body", [None, {}, [], "dark"])
def test_a_body_with_no_preference_is_refused(client, body) -> None:
    response = client.put("/api/preferences", json=body)

    assert response.status_code == 400
    assert response.get_json()["code"] == "preference_invalid"


def test_an_unsaved_preference_reports_what_is_stored(tmp_path, monkeypatch) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv(preferences.STATE_DIR_ENV, str(blocker / "state"))
    app = Flask(__name__)
    register_api(app)
    client = app.test_client()

    response = client.put("/api/preferences", json={"theme": "dark"})

    # Nothing could be written, so the answer reports the unchanged state rather
    # than claiming the choice was saved.
    assert response.status_code == 200
    assert response.get_json() == {}
