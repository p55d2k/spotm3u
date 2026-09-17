"""Tests for the Flask application scaffold."""

from spotm3u.app import create_app


def test_homepage_renders() -> None:
    client = create_app().test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b"Spotify to M3U" in response.data


def test_static_stylesheet_is_available() -> None:
    client = create_app().test_client()

    response = client.get("/static/style.css")

    assert response.status_code == 200
    assert b"font-family" in response.data
