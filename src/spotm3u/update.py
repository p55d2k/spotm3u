"""In-app update checks against the GitHub Releases API.

SpotM3U never silently self-replaces a running application: a packaged build
cannot reliably overwrite its own frozen bundle (the executable is locked while
running, installers need elevation, and replacing files under a live app risks
corruption). Instead this module detects a newer published release, reports it
to the web UI, and lets the user download the correct platform asset so they
can install it when it suits them.

The check is deliberately non-fatal. Network failures, rate limits, unknown
assets and missing releases all degrade to "no update known" instead of
raising, so a stale or offline machine keeps working exactly as before.
"""

from __future__ import annotations

import logging
import platform
import re
import sys
import time
from dataclasses import dataclass
from typing import Any

import requests

from .log import PACKAGE_LOGGER

_LOGGER = logging.getLogger(PACKAGE_LOGGER)

DEFAULT_REPO = "p55d2k/spotm3u"
API_URL = "https://api.github.com/repos/{repo}/releases/latest"
REQUEST_TIMEOUT = 10.0
_UA = {
    "User-Agent": "SpotM3U update check",
    "Accept": "application/vnd.github+json",
}
_VERSION_RE = re.compile(r"^[vV]?(\d+)\.(\d+)\.(\d+)(?:[.-].*)?$")

# Module-level memoisation so a multi-page session does not hammer the GitHub
# API. Keyed by repository; entries are only held for the configured interval.
_LAST_CHECK: dict[str, tuple[float, UpdateCheck]] = {}


@dataclass(frozen=True)
class UpdateCheck:
    """Snapshot of the latest known release relative to the running version."""

    current_version: str
    latest_version: str | None
    update_available: bool
    release_url: str | None
    asset_url: str | None
    asset_name: str | None
    release_name: str | None
    published_at: str | None
    checked_at: int
    error: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "update_available": self.update_available,
            "release_url": self.release_url,
            "asset_url": self.asset_url,
            "asset_name": self.asset_name,
            "release_name": self.release_name,
            "published_at": self.published_at,
            "checked_at": self.checked_at,
            "error": self.error,
        }


def parse_version(value: str) -> tuple[int, int, int] | None:
    """Parse ``v1.2.3`` style versions into a sortable 3-tuple.

    Returns ``None`` when the value is not a plain semantic version, so
    unusual tags never crash the comparison.
    """
    match = _VERSION_RE.match(value)
    if match is None:
        return None
    return tuple(int(group) for group in match.groups())  # type: ignore[return-value]


def is_newer(latest: str, current: str) -> bool:
    """True when ``latest`` is a strict semantic-version upgrade over ``current``."""
    latest_version = parse_version(latest)
    current_version = parse_version(current)
    if latest_version is None or current_version is None:
        return False
    return latest_version > current_version


def platform_asset_kind() -> tuple[str, str, str]:
    """The release-asset platform, arch and preferred extension for this OS.

    Releases are named ``SpotM3U-<version>-<platform>-<arch>.zip`` on Windows
    and Linux, and ``SpotM3U-<version>-macos-<arch>.pkg`` on macOS (the PKG is
    the reliable macOS artifact for the unsigned app; see docs/releases.md).
    """
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x86_64"
    if sys.platform == "darwin":
        return "macos", arch, ".pkg"
    if sys.platform.startswith("win"):
        return "windows", "x86_64", ".zip"
    return "linux", "x86_64", ".zip"


def select_asset(
    assets: list[dict[str, Any]], version: str, asset_kind: tuple[str, str, str] | None = None
) -> tuple[str, str] | None:
    """Pick the downloadable asset for this platform from a release payload.

    Returns ``(asset name, browser download URL)`` or ``None`` when no asset
    matches. An exact platform/arch match is preferred; a fallback matches any
    asset for the platform so an unsupported architecture still gets pointed at
    a usable download.
    """
    platform_name, arch, extension = asset_kind or platform_asset_kind()
    exact = f"SpotM3U-v{version}-{platform_name}-{arch}{extension}"
    for asset in assets:
        name = str(asset.get("name", ""))
        if name == exact:
            return name, str(asset.get("browser_download_url", ""))
    for asset in assets:
        name = str(asset.get("name", ""))
        if name.startswith(f"SpotM3U-v{version}-{platform_name}-") and name.endswith(extension):
            return name, str(asset.get("browser_download_url", ""))
    return None


def _fetch_latest(repo: str, timeout: float) -> dict[str, Any] | str:
    """Query the GitHub API for the latest release, returning a dict or error text."""
    try:
        response = requests.get(API_URL.format(repo=repo), headers=_UA, timeout=timeout)
    except requests.RequestException as error:
        _LOGGER.info("update check failed to contact GitHub: %s", error)
        return "Update check could not reach GitHub."
    if response.status_code == 404:
        _LOGGER.info("update check: repository %s has no releases", repo)
        return "Update check found no releases."
    if response.status_code != 200:
        _LOGGER.info("update check: GitHub API returned HTTP %s", response.status_code)
        return "Update check is temporarily unavailable."
    try:
        return response.json()
    except ValueError:
        return "Update check returned an unreadable response."


def check_for_updates(
    *,
    repo: str = DEFAULT_REPO,
    current_version: str,
    interval_seconds: int = 24 * 60 * 60,
    timeout: float = REQUEST_TIMEOUT,
) -> UpdateCheck:
    """Return the latest release relative to ``current_version``, cached by interval."""
    now = int(time.time())
    cached = _LAST_CHECK.get(repo)
    if cached is not None and now - cached[0] < interval_seconds:
        return cached[1]

    latest = _fetch_latest(repo, timeout)
    if isinstance(latest, str):
        result = UpdateCheck(
            current_version=current_version,
            latest_version=None,
            update_available=False,
            release_url=None,
            asset_url=None,
            asset_name=None,
            release_name=None,
            published_at=None,
            checked_at=now,
            error=latest,
        )
    else:
        tag = str(latest.get("tag_name", ""))
        version = tag.lstrip("v") if tag.startswith("v") else tag
        assets = latest.get("assets") or []
        asset = select_asset(assets, version) if version else None
        result = UpdateCheck(
            current_version=current_version,
            latest_version=version or None,
            update_available=bool(version) and is_newer(version, current_version),
            release_url=latest.get("html_url"),
            asset_url=asset[1] if asset else None,
            asset_name=asset[0] if asset else None,
            release_name=latest.get("name"),
            published_at=latest.get("published_at"),
            checked_at=now,
            error=None,
        )
    _LAST_CHECK[repo] = (now, result)
    return result
