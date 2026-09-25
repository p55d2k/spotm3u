"""Opt-in, network-dependent matching regression checks.

These tests exercise the real search/ranking path against live YouTube
results. They are separated from ``test_matching_regression.py`` because they
require network access and are not part of the normal suite.

Run them explicitly with::

    SPOTM3U_RUN_NETWORK_TESTS=1 uv run pytest -m network

Both a custom marker and the environment guard are used so a plain
``pytest`` run skips them even when the marker is selected by accident.
"""

from __future__ import annotations

import os

import pytest

from spotm3u.models import Track
from spotm3u.online import OnlineSourceSearcher, rank_source_candidates

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("SPOTM3U_RUN_NETWORK_TESTS") != "1",
        reason="set SPOTM3U_RUN_NETWORK_TESTS=1 to run live matching tests",
    ),
]


@pytest.mark.parametrize(
    ("title", "artists"),
    [
        pytest.param("海闊天空", ["Beyond"], id="cjk-traditional"),
        pytest.param("Higher Power", ["Coldplay"], id="latin"),
    ],
)
def test_live_search_finds_an_accepted_candidate(title: str, artists: list[str]) -> None:
    track = Track(title=title, artists=artists)

    candidates = OnlineSourceSearcher(max_results=5).search(track)

    assert candidates, "live search returned no candidates"
    ranked = rank_source_candidates(track, candidates)
    assert any(result.accepted for result in ranked)
