"""YouTube download diagnostics: PO Token setup, provider checks, reports.

The downloader calls into this module before a YouTube download and when a
download fails; it renders the actionable setup instructions that surface in the
CLI and the web UI. Nothing here touches the filesystem layout of downloads.
"""

from __future__ import annotations

import logging
import shutil
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from json import JSONDecodeError, loads
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from .errors import DownloadError, _redact_error, _redact_url

logger = logging.getLogger(__name__)

# yt-dlp / bgutil integration identifiers. The plugin provides PO Token
# providers to yt-dlp; the provider server/script is a separate component that
# the plugin talks to. spotm3u never generates, caches, or stores PO tokens.
PO_TOKEN_PLUGIN_MODULE = "yt_dlp_plugins.extractor.getpot_bgutil"
BGUTIL_DISTRIBUTION = "bgutil-ytdlp-pot-provider"
YTDLP_DISTRIBUTION = "yt-dlp"

# Expected bgutil 2.x provider artifacts (runtime, script path relative to home).
_SCRIPT_PROVIDER_CANDIDATES = (
    ("node", Path("build") / "generate_once.js"),
    ("deno", Path("src") / "generate_once.ts"),
)

# Seconds to wait for a configured bgutil HTTP provider to answer its ``/ping``
# probe. A provider that is still starting up can be slower than this default,
# and a false "not running or reachable" would send the user hunting for a
# problem they do not have, so it is configurable through
# ``download.pot_provider_timeout``.
_POT_PROVIDER_TIMEOUT = 5.0


def set_pot_provider_timeout(seconds: int) -> None:
    """Set how long to wait for a configured bgutil HTTP provider to answer."""
    global _POT_PROVIDER_TIMEOUT
    _POT_PROVIDER_TIMEOUT = float(seconds)


def _validate_pot_provider(
    provider_url: str | None,
    provider_home: str | None,
) -> None:
    """Fail before yt-dlp starts when an explicitly configured provider is unusable."""
    if provider_url and provider_home:
        raise DownloadError("configure either a bgutil HTTP URL or a script home, not both")
    if find_spec(PO_TOKEN_PLUGIN_MODULE) is None:
        raise DownloadError(_po_token_plugin_message())
    if provider_url:
        _validate_http_provider(provider_url)
    elif provider_home:
        _validate_script_provider(provider_home)


def _installed_bgutil_version() -> str:
    return _distribution_version(BGUTIL_DISTRIBUTION)


def _installed_ytdlp_version() -> str:
    return _distribution_version(YTDLP_DISTRIBUTION)


def _distribution_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "unknown"


def _po_token_plugin_message() -> str:
    return (
        "The bgutil yt-dlp PO Token plugin is not installed, so YouTube PO "
        "Tokens cannot be generated. Install bgutil-ytdlp-pot-provider and "
        "restart SpotM3U; installing the plugin does not start the separate "
        "provider server."
    )


def _validate_http_provider(provider_url: str) -> None:
    """Check an explicitly configured bgutil HTTP provider before yt-dlp runs."""
    parsed = urlparse(provider_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DownloadError("configured bgutil HTTP provider URL is invalid")
    try:
        payload = _fetch_provider_ping(provider_url)
    except (HTTPError, URLError, TimeoutError, OSError, JSONDecodeError, ValueError) as exc:
        # The message intentionally omits the URL so embedded credentials cannot leak.
        raise DownloadError(
            "the configured bgutil HTTP provider is not running or reachable; start it and retry"
        ) from exc
    provider_version = payload.get("version")
    expected = _installed_bgutil_version()
    if not _provider_version_compatible(provider_version, expected):
        raise DownloadError(
            "the configured bgutil HTTP provider is incompatible: it reports "
            f"version {provider_version or 'unknown'} but the installed bgutil "
            f"plugin is {expected}; install matching major versions of the "
            "plugin and provider"
        )
    if provider_version != expected:
        logger.warning(
            "bgutil HTTP provider version %s differs from the installed plugin "
            "version %s; matching versions are recommended",
            provider_version,
            expected,
        )


def _fetch_provider_ping(provider_url: str) -> dict:
    """Fetch the bgutil HTTP provider ``/ping`` payload."""
    ping_url = urljoin(provider_url.rstrip("/") + "/", "ping")
    with urlopen(Request(ping_url), timeout=_POT_PROVIDER_TIMEOUT) as response:
        payload = loads(response.read())
    if not isinstance(payload, dict):
        raise ValueError("unexpected bgutil provider response")
    return payload


def _provider_version_compatible(provider_version: object, expected: str) -> bool:
    """A provider is compatible when its major version matches the plugin's."""

    def _major(value: object) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        return value.strip().split(".", 1)[0]

    provider_major = _major(provider_version)
    return provider_major is not None and provider_major == _major(expected)


def _validate_script_provider(provider_home: str) -> None:
    """Check a configured bgutil script checkout without executing it."""
    home = Path(provider_home).expanduser()
    if not home.is_dir():
        raise DownloadError("the configured bgutil script provider directory does not exist")
    if not _script_provider_available(home):
        raise DownloadError(
            "the configured bgutil script provider is not usable; install Node.js "
            "22+ or Deno 2.4.3+ and build the provider script (for example "
            "`npm ci && npx tsc`), then retry"
        )


def _script_provider_available(home: Path) -> bool:
    """True when a built provider script has its matching runtime on PATH."""
    return any(
        (home / relative).is_file() and shutil.which(runtime) is not None
        for runtime, relative in _SCRIPT_PROVIDER_CANDIDATES
    )


def _po_token_message(*, provider_configured: bool) -> str:
    if find_spec(PO_TOKEN_PLUGIN_MODULE) is None:
        return _po_token_plugin_message()
    if provider_configured:
        return (
            "the configured bgutil PO Token provider failed while generating a "
            "token. Verify the provider server or script is running and that its "
            "version matches the installed bgutil plugin, then retry. PO tokens "
            "do not replace browser authentication."
        )
    return (
        "yt-dlp reported a PO Token problem but no bgutil provider is configured. "
        "Start the bgutil HTTP provider or set download.pot_provider_home, then "
        "retry; PO tokens do not replace browser authentication."
    )


def _cookie_required_message(browser: str | None, *, extraction_failed: bool = False) -> str:
    if browser:
        problem = (
            "their cookies could not be read"
            if extraction_failed
            else "a signed-in session is required"
        )
        return (
            f"YouTube requires browser authentication, but {problem} from {browser}. "
            "Sign in to YouTube in that browser, close it so the cookie database "
            "is not locked, verify the profile, or choose another browser in "
            "download.cookies_from_browser (or SPOTM3U_YTDLP_BROWSER) and retry."
        )
    return (
        "YouTube is asking for a signed-in browser session for this download; a "
        "PO token does not replace authentication. Sign in to YouTube in Chrome, "
        "Chromium, Firefox, Safari, Edge, Brave, Opera, Vivaldi, or Whale, then "
        "set download.cookies_from_browser in config.toml (or "
        "SPOTM3U_YTDLP_BROWSER) and retry."
    )


def describe_youtube_setup(
    *,
    cookies_from_browser: str | None = None,
    pot_provider_url: str | None = None,
    pot_provider_home: str | None = None,
) -> dict[str, object]:
    """Report the YouTube authentication / PO-token setup without secrets.

    This is an integration diagnostic: it answers whether the bgutil plugin is
    importable, whether a configured provider answers ``/ping``, and which
    browser (if any) yt-dlp will read cookies from. It never reads, generates,
    stores, or returns PO tokens, and it never raises for an unreachable
    provider so callers can log a complete picture.
    """
    plugin_installed = find_spec(PO_TOKEN_PLUGIN_MODULE) is not None
    notes: list[str] = []
    report: dict[str, object] = {
        "yt_dlp_version": _installed_ytdlp_version(),
        "bgutil_plugin_installed": plugin_installed,
        "bgutil_plugin_version": _installed_bgutil_version(),
        "cookies_from_browser": (cookies_from_browser.casefold() if cookies_from_browser else None),
        "pot_provider_url": _redact_url(pot_provider_url),
        "pot_provider_home": pot_provider_home,
        "http_provider": None,
        "script_provider": None,
        "notes": notes,
    }
    if not plugin_installed:
        notes.append("The bgutil yt-dlp plugin is missing, so PO tokens cannot be generated.")
    if pot_provider_url:
        report["http_provider"] = _probe_http_provider(pot_provider_url)
    if pot_provider_home:
        report["script_provider"] = _probe_script_provider(pot_provider_home)
    if not (pot_provider_url or pot_provider_home):
        notes.append(
            "No bgutil provider is configured; yt-dlp will use its default provider discovery."
        )
    if not cookies_from_browser:
        notes.append(
            "No browser is configured; authenticated YouTube videos will require "
            "download.cookies_from_browser."
        )
    return report


def _probe_http_provider(provider_url: str) -> dict[str, object]:
    """Best-effort report for a configured bgutil HTTP provider."""
    result: dict[str, object] = {
        "url": _redact_url(provider_url),
        "reachable": False,
        "version": None,
        "compatible": None,
    }
    parsed = urlparse(provider_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        result["error"] = "invalid provider URL"
        return result
    try:
        payload = _fetch_provider_ping(provider_url)
    except Exception as exc:  # noqa: BLE001 - diagnostics must never raise
        result["error"] = _redact_error(f"{type(exc).__name__}: {exc}")
        return result
    provider_version = payload.get("version")
    result["reachable"] = True
    result["version"] = provider_version if isinstance(provider_version, str) else None
    result["compatible"] = _provider_version_compatible(
        provider_version, _installed_bgutil_version()
    )
    return result


def _probe_script_provider(provider_home: str) -> dict[str, object]:
    """Best-effort report for a configured bgutil script checkout."""
    home = Path(provider_home).expanduser()
    result: dict[str, object] = {
        "home": str(home),
        "runtime": None,
        "script": None,
        "available": False,
    }
    if not home.is_dir():
        result["error"] = "provider directory does not exist"
        return result
    for runtime, relative in _SCRIPT_PROVIDER_CANDIDATES:
        script = home / relative
        if script.is_file() and shutil.which(runtime) is not None:
            result["runtime"] = runtime
            result["script"] = str(script)
            result["available"] = True
            return result
    result["error"] = "no built provider script with a matching runtime was found"
    return result
