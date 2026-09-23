"""Tests for in-app GitHub release update checks."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from spotm3u.app import create_app
from spotm3u.update import (
    check_for_updates,
    is_newer,
    parse_version,
    select_asset,
)


def reset_cache(monkeypatch) -> None:
    from spotm3u import update

    monkeypatch.setattr(update, "_LAST_CHECK", {})
    return update


def _release(
    *,
    tag: str = "v1.2.3",
    name: str | None = None,
    assets: list[tuple[str, str]] | None = None,
) -> dict:
    items = []
    for asset_name, url in assets or []:
        items.append({"name": asset_name, "browser_download_url": url})
    return {
        "tag_name": tag,
        "name": name or tag,
        "html_url": f"https://github.com/p55d2k/spotm3u/releases/tag/{tag}",
        "published_at": "2026-09-01T00:00:00Z",
        "assets": items,
    }


def test_parse_version_handles_v_prefix_and_suffixes() -> None:
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.2.3") == (1, 2, 3)
    assert parse_version("0.1.0") == (0, 1, 0)
    assert parse_version("v0.1.0-dev") == (0, 1, 0)
    assert parse_version("nonsense") is None


def test_is_newer_compares_semantic_versions() -> None:
    assert is_newer("1.2.3", "1.2.2") is True
    assert is_newer("1.2.3", "1.3.0") is False
    assert is_newer("1.2.3", "1.2.3") is False
    assert is_newer("not-a-version", "1.2.3") is False
    assert is_newer("1.2.3", "not-a-version") is False


def test_platform_asset_kind_matches_release_naming(monkeypatch) -> None:
    update = reset_cache(monkeypatch)
    monkeypatch.setattr(update.sys, "platform", "darwin")
    monkeypatch.setattr(update.platform, "machine", lambda: "arm64")
    assert update.platform_asset_kind() == ("macos", "arm64", ".pkg")

    monkeypatch.setattr(update.sys, "platform", "win32")
    assert update.platform_asset_kind() == ("windows", "x86_64", ".zip")

    monkeypatch.setattr(update.sys, "platform", "linux")
    monkeypatch.setattr(update.platform, "machine", lambda: "x86_64")
    assert update.platform_asset_kind() == ("linux", "x86_64", ".zip")


def test_select_asset_prefers_exact_platform_and_arch() -> None:
    assets = [
        {
            "name": "SpotM3U-v1.2.3-macos-x86_64.pkg",
            "browser_download_url": "https://example/a.pkg",
        },
        {"name": "SpotM3U-v1.2.3-macos-arm64.pkg", "browser_download_url": "https://example/b.pkg"},
    ]
    assert select_asset(assets, "1.2.3", ("macos", "arm64", ".pkg")) == (
        "SpotM3U-v1.2.3-macos-arm64.pkg",
        "https://example/b.pkg",
    )
    assert select_asset(assets, "1.2.3", ("macos", "x86_64", ".pkg")) == (
        "SpotM3U-v1.2.3-macos-x86_64.pkg",
        "https://example/a.pkg",
    )


def test_select_asset_falls_back_to_any_platform_asset() -> None:
    assets = [
        {"name": "SpotM3U-v2.0.0-macos-arm64.pkg", "browser_download_url": "https://example/c.pkg"}
    ]
    assert select_asset(assets, "2.0.0", ("macos", "x86_64", ".pkg")) == (
        "SpotM3U-v2.0.0-macos-arm64.pkg",
        "https://example/c.pkg",
    )


def test_select_asset_returns_none_without_a_match() -> None:
    assets = [{"name": "SpotM3U-v2.0.0-windows-x86_64.zip", "browser_download_url": "https://x"}]
    assert select_asset(assets, "2.0.0", ("linux", "x86_64", ".zip")) is None
    assert select_asset([], "1.0.0", ("macos", "arm64", ".pkg")) is None


def test_check_for_updates_reports_newer_release(monkeypatch) -> None:
    update = reset_cache(monkeypatch)

    def fake_get(url: str, **kwargs):
        assert kwargs["timeout"] > 0
        body = _release(assets=[("SpotM3U-v1.2.3-macos-arm64.pkg", "https://example/a.pkg")])
        return SimpleNamespace(status_code=200, json=lambda: body)

    monkeypatch.setattr(update.requests, "get", fake_get)
    monkeypatch.setattr(update.sys, "platform", "darwin")
    monkeypatch.setattr(update.platform, "machine", lambda: "arm64")

    result = check_for_updates(repo="p55d2k/spotm3u", current_version="1.1.0")

    assert result.update_available is True
    assert result.latest_version == "1.2.3"
    assert result.asset_name == "SpotM3U-v1.2.3-macos-arm64.pkg"
    assert result.asset_url == "https://example/a.pkg"
    assert result.release_url.endswith("/releases/tag/v1.2.3")
    assert result.error is None
    assert result.as_dict()["update_available"] is True


def _offline():
    raise requests.ConnectionError("offline")


@pytest.mark.parametrize(
    ("respond", "current_version", "latest_version", "expect_error"),
    [
        pytest.param(
            lambda: SimpleNamespace(status_code=200, json=lambda: _release(tag="v1.2.3")),
            "1.2.3",
            "1.2.3",
            False,
            id="current-is-latest",
        ),
        pytest.param(_offline, "1.0.0", None, True, id="network-error"),
        pytest.param(
            lambda: SimpleNamespace(status_code=503, json=lambda: {}),
            "1.0.0",
            None,
            True,
            id="http-error",
        ),
        pytest.param(
            lambda: SimpleNamespace(status_code=200, json=lambda: {}),
            "1.0.0",
            None,
            False,
            id="empty-release",
        ),
    ],
)
def test_check_for_updates_degrades_without_a_newer_release(
    monkeypatch, respond, current_version, latest_version, expect_error
) -> None:
    update = reset_cache(monkeypatch)
    monkeypatch.setattr(update.requests, "get", lambda url, **kwargs: respond())

    result = check_for_updates(repo="p55d2k/spotm3u", current_version=current_version)

    assert result.update_available is False
    assert result.latest_version == latest_version
    assert bool(result.error) is expect_error


def test_check_route_with_an_available_update(monkeypatch) -> None:
    update = reset_cache(monkeypatch)

    def fake_get(url: str, **kwargs):
        return SimpleNamespace(
            status_code=200,
            json=lambda: _release(
                tag="v2.0.0",
                assets=[("SpotM3U-v2.0.0-linux-x86_64.zip", "https://example/d.zip")],
            ),
        )

    monkeypatch.setattr(update.requests, "get", fake_get)
    monkeypatch.setattr(update.sys, "platform", "linux")

    client = create_app().test_client()
    response = client.get("/update/check")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["update_available"] is True
    assert payload["latest_version"] == "2.0.0"
    assert payload["current_version"] == "0.1.0"
    assert payload["asset_url"] == "https://example/d.zip"


def test_check_route_honours_disabled_updates(monkeypatch) -> None:
    reset_cache(monkeypatch)
    client = create_app({"UPDATE_CHECK": False}).test_client()

    response = client.get("/update/check")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["update_available"] is False
    assert "disabled" in payload.get("error", "")


def test_check_route_caches_within_interval(monkeypatch) -> None:
    update = reset_cache(monkeypatch)
    calls = []

    def fake_get(url: str, **kwargs):
        calls.append(url)
        return SimpleNamespace(status_code=200, json=lambda: _release(tag="v2.0.0"))

    monkeypatch.setattr(update.requests, "get", fake_get)
    app = create_app({"UPDATE_CHECK_INTERVAL_HOURS": 24})
    client = app.test_client()

    first = client.get("/update/check").get_json()
    second = client.get("/update/check").get_json()

    assert first["update_available"] is True
    assert second == first
    assert len(calls) == 1


def test_the_update_notice_ships_in_the_sidebar_footer() -> None:
    client = create_app().test_client()

    response = client.get("/")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    # The control itself ships in the page, not just the script that drives it:
    # a script querying an element that is never rendered is how the notice
    # silently stopped appearing once already.
    assert 'class="button button-ghost update-notice"' in page
    assert "data-update-label>" in page
    assert "/update/check" in page
    # It belongs in the sidebar footer beside the theme toggle. As a page-level
    # banner it pushed the whole page down every time the check resolved.
    assert "data-update-notice" in page.split('class="sidebar-footer"', 1)[1]
    sidebar = (Path(create_app().root_path) / "templates" / "_sidebar.html").read_text(
        encoding="utf-8"
    )
    assert '{% include "_update_notice.html" %}' in sidebar
    header = (Path(create_app().root_path) / "templates" / "_header.html").read_text(
        encoding="utf-8"
    )
    assert "_update_notice.html" not in header
