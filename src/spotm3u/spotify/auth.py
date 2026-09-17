"""Compatibility shim for the Spotify authentication module."""

from .authentication import (
    AUTHENTICATION_COOKIE,
    SPOTIFY_HOME_URL,
    AuthenticationTimeoutError,
    SpotifyAuthenticator,
)

__all__ = [
    "AUTHENTICATION_COOKIE",
    "SPOTIFY_HOME_URL",
    "AuthenticationTimeoutError",
    "SpotifyAuthenticator",
]
