"""Shared test fixtures and helpers."""

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
import requests

from spotm3u import artwork

# The repository root, for the tests that inspect files outside ``tests/``
# (packaging scripts, ``config.toml``, ``assets/``). Deriving it here keeps the
# tests independent of how deeply they are nested under ``tests/``.
REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def no_network_artwork(monkeypatch):
    """Keep artwork enrichment offline unless a test installs its own mock.

    Resolution jobs enrich resolved tracks with artwork; without this fixture
    those jobs would make real MusicBrainz/iTunes requests during unrelated
    tests. Tests that exercise artwork lookup override ``requests.get``
    themselves.
    """

    def unreachable(*_args, **_kwargs):
        raise requests.ConnectionError("network disabled in tests")

    monkeypatch.setattr("spotm3u.artwork_sources.requests.get", unreachable)


@pytest.fixture(autouse=True)
def no_network_lyrics(monkeypatch):
    """Keep lyrics enrichment offline unless a test installs its own stub.

    Metadata enrichment asks the ``syncedlyrics`` library for lyrics; without
    this fixture unrelated tests would query real lyrics providers. Tests that
    exercise lyrics override ``spotm3u.lyrics.syncedlyrics.search``.
    """

    def unreachable(*_args, **_kwargs):
        raise requests.ConnectionError("network disabled in tests")

    monkeypatch.setattr("spotm3u.lyrics.syncedlyrics.search", unreachable)


@pytest.fixture
def verify_local_toggle():
    """Run a test with the artwork verification flag, restoring the default after."""

    yield
    artwork.set_artwork_verify_local(True)


def export_zip() -> bytes:
    """A minimal Exportify archive with two playlists, "First" and "Second"."""
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            "one.csv",
            "Track Name,Artist Name(s)\nFirst,Artist\n",
        )
        archive.writestr(
            "two.csv",
            "Track Name,Artist Name(s)\nSecond,Artist\n",
        )
    return output.getvalue()


class NoCandidates:
    def search(self, track):
        return ()

    def search_query(self, track, query):
        return ()
