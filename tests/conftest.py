"""Shared test fixtures."""

import pytest
import requests


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

    monkeypatch.setattr("spotm3u.metadata.requests.get", unreachable)


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
