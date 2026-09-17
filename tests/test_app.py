"""Tests for the Flask application scaffold."""

from spotm3u.app import create_app


def test_homepage_renders() -> None:
    client = create_app().test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b"Spotify to M3U" in response.data
    assert b"Exportify" in response.data
    assert b"Export All" in response.data
    assert b"Download the playlist export ZIP" in response.data
    assert b'action="/upload"' in response.data
    assert b'accept=".zip,application/zip"' in response.data


def test_static_stylesheet_is_available() -> None:
    client = create_app().test_client()

    response = client.get("/static/style.css")

    assert response.status_code == 200
    assert b"font-family" in response.data
