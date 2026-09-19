"""Download validated online sources into a controlled MP3 directory."""

from __future__ import annotations

import logging
import re
import shutil
import threading
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from json import JSONDecodeError, loads
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from ..log import track_identifier
from ..metadata import MetadataError, enrich_metadata
from ..models import Track
from ._ytdlp import ensure_ytdlp_plugins_loaded
from .audio_validation import validate_downloaded_audio

logger = logging.getLogger(__name__)

# yt-dlp / bgutil integration identifiers. The plugin provides PO Token
# providers to yt-dlp; the provider server/script is a separate component that
# the plugin talks to. spotm3u never generates, caches, or stores PO tokens.
PO_TOKEN_PLUGIN_MODULE = "yt_dlp_plugins.extractor.getpot_bgutil"
BGUTIL_DISTRIBUTION = "bgutil-ytdlp-pot-provider"
YTDLP_DISTRIBUTION = "yt-dlp"

# YouTube failure categories. Each maps to a different, actionable setup or
# retry instruction, so they are deliberately not collapsed into one result.
AUTHENTICATION_REQUIRED = "authentication_required"
BROWSER_COOKIE_ERROR = "browser_cookie_error"
PO_TOKEN_UNAVAILABLE = "po_token_unavailable"
CONTENT_UNAVAILABLE = "content_unavailable"
RATE_LIMITED = "rate_limited"
DOWNLOAD_FAILURE = "download_failure"

# Conservative marker sets. These are matched against the text yt-dlp currently
# emits (verified against the pinned yt-dlp release) plus structured extractor
# error fields, never against a bare "bot" substring.
_PO_TOKEN_MARKERS = (
    "po token",
    "pot token",
    "po_token",
    "pot_token",
    "proof-of-origin",
    "proof of origin",
    "getpot",
    "bgutil",
    "provider is not available",
    "provider unavailable",
    "server is not available",
)
_COOKIE_FAILURE_MARKERS = (
    "could not copy",
    "cookies database",
    "failed to decrypt",
    "could not be decrypted",
    "failed to load cookies",
    "could not read containers.json",
    "could not find firefox container",
)
_AUTHENTICATION_MARKERS = (
    "login_required",
    "login required",
    "log in required",
    "sign in to confirm",
    "sign in required",
    "please sign in",
    "authentication required",
    "use --cookies-from-browser or --cookies for the authentication",
    "only available for registered users",
)
_RATE_LIMIT_MARKERS = (
    "rate-limited by youtube",
    "rate limited",
    "rate limit",
    "try again later",
    "captcha",
    "ip is likely being blocked",
    "unusual traffic",
    "http error 429",
    "too many requests",
)
_CONTENT_UNAVAILABLE_MARKERS = (
    "private video",
    "members-only",
    "members only",
    "join this channel",
    "age-restricted",
    "age restricted",
    "confirm your age",
    "video unavailable",
    "removed by the uploader",
    "not available in your country",
    "geo-restricted",
    "geo restricted",
    "available in your location",
    "this video is not available",
)

# Expected bgutil 2.x provider artifacts (runtime, script path relative to home).
_SCRIPT_PROVIDER_CANDIDATES = (
    ("node", Path("build") / "generate_once.js"),
    ("deno", Path("src") / "generate_once.ts"),
)

# Per-output-path lock so concurrent downloads targeting the same local file
# (same track duplicate or explicit overwrite) serialize rather than corrupt.
_OUTPUT_LOCKS: dict[str, threading.Lock] = {}
_OUTPUT_LOCKS_GUARD = threading.Lock()


class _InFlightDownload:
    """Ownership record for one in-progress download of an output path."""

    __slots__ = ("condition", "done", "path", "error")

    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.done = False
        self.path: Path | None = None
        self.error: BaseException | None = None


# Single-flight registry: parallel jobs that share a recording (for example two
# batch playlists, or a playlist that lists the same track twice) wait for the
# first download of an output path instead of each downloading it again.
_OUTPUT_IN_FLIGHT: dict[str, _InFlightDownload] = {}
_OUTPUT_IN_FLIGHT_GUARD = threading.Lock()


class DownloadError(RuntimeError):
    """Raised when a source cannot be downloaded into a complete MP3 file."""


def download_track(
    track: Track,
    source_url: str,
    output_dir: str | Path,
    *,
    quality: str = "192",
    retries: int = 5,
    fragment_retries: int = 5,
    socket_timeout: int = 30,
    timeout: float | None = 600,
    cookies_from_browser: str | None = None,
    pot_provider_url: str | None = None,
    pot_provider_home: str | None = None,
) -> Path:
    """Download ``source_url`` and return its verified local MP3 path.

    ``output_dir`` is the caller-owned job/library directory. All yt-dlp
    intermediate files and the final file are constrained to that directory.

    ``timeout`` bounds the entire download wall-clock time so a hung yt-dlp or
    ffmpeg run cannot occupy a worker forever. It is raised as a
    :class:`DownloadError`; ``socket_timeout`` remains the network-level
    fallback that bounds each socket operation.
    """
    _validate_source_url(source_url)
    destination = Path(output_dir).expanduser().resolve()
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DownloadError(f"cannot create download directory: {destination}") from exc

    output_path = destination / _output_name(track, source_url)
    return _download_single_flight(
        track,
        source_url,
        destination,
        output_path,
        quality=quality,
        retries=retries,
        fragment_retries=fragment_retries,
        socket_timeout=socket_timeout,
        timeout=timeout,
        cookies_from_browser=cookies_from_browser,
        pot_provider_url=pot_provider_url,
        pot_provider_home=pot_provider_home,
    )


def _download_single_flight(
    track: Track,
    source_url: str,
    destination: Path,
    output_path: Path,
    *,
    quality: str,
    retries: int,
    fragment_retries: int,
    socket_timeout: int,
    timeout: float | None,
    cookies_from_browser: str | None,
    pot_provider_url: str | None,
    pot_provider_home: str | None,
) -> Path:
    """Download ``output_path`` exactly once no matter how many callers race.

    The first caller for a path becomes its owner and performs the download;
    every concurrent caller targeting the same path waits for the owner and
    reuses the validated file instead of downloading it again. A peer's file
    is only reused when it still passes audio validation against the waiting
    track. If the owner failed (or its file is unusable for this track), the
    next caller acquires ownership and downloads itself.
    """
    key = str(output_path.resolve())
    while True:
        with _OUTPUT_IN_FLIGHT_GUARD:
            in_flight = _OUTPUT_IN_FLIGHT.get(key)
            if in_flight is None:
                in_flight = _InFlightDownload()
                _OUTPUT_IN_FLIGHT[key] = in_flight
                owner = True
            else:
                owner = False

        if owner:
            return _finish_in_flight(
                key,
                in_flight,
                lambda: _perform_download(
                    track,
                    source_url,
                    destination,
                    output_path,
                    quality=quality,
                    retries=retries,
                    fragment_retries=fragment_retries,
                    socket_timeout=socket_timeout,
                    timeout=timeout,
                    cookies_from_browser=cookies_from_browser,
                    pot_provider_url=pot_provider_url,
                    pot_provider_home=pot_provider_home,
                ),
            )

        reused = _await_peer(in_flight, track, timeout)
        if reused is not None:
            logger.info(
                "download track=%s url=%s status=reused from concurrent peer path=%s",
                track_identifier(track),
                _redact_url(source_url) or source_url,
                reused,
            )
            return reused


def _finish_in_flight(
    key: str,
    in_flight: _InFlightDownload,
    download: Callable[[], Path],
) -> Path:
    """Run ``download`` as the owner and publish its outcome to waiting peers."""
    try:
        path = download()
    except BaseException as exc:  # noqa: BLE001 - peers must always be released
        with in_flight.condition:
            in_flight.done = True
            in_flight.error = exc
            in_flight.condition.notify_all()
        with _OUTPUT_IN_FLIGHT_GUARD:
            _OUTPUT_IN_FLIGHT.pop(key, None)
        raise
    with in_flight.condition:
        in_flight.done = True
        in_flight.path = path
        in_flight.condition.notify_all()
    with _OUTPUT_IN_FLIGHT_GUARD:
        _OUTPUT_IN_FLIGHT.pop(key, None)
    return path


def _await_peer(
    in_flight: _InFlightDownload,
    track: Track,
    timeout: float | None,
) -> Path | None:
    """Wait for the owner of ``in_flight`` and reuse a validated result.

    Returns the owner's path when it produced a file that still passes audio
    validation against ``track``; ``None`` means the caller must download the
    file itself.
    """
    with in_flight.condition:
        in_flight.condition.wait_for(lambda: in_flight.done, timeout=timeout)
        if (
            in_flight.done
            and in_flight.path is not None
            and in_flight.path.is_file()
            and validate_downloaded_audio(track, in_flight.path).status == "valid"
        ):
            return in_flight.path
    return None


def _perform_download(
    track: Track,
    source_url: str,
    destination: Path,
    output_path: Path,
    *,
    quality: str,
    retries: int,
    fragment_retries: int,
    socket_timeout: int,
    timeout: float | None,
    cookies_from_browser: str | None,
    pot_provider_url: str | None,
    pot_provider_home: str | None,
) -> Path:
    """Run the actual yt-dlp/ffmpeg download under the per-path lock."""
    if timeout is not None and timeout > 0:
        try:
            return _run_with_timeout(
                timeout,
                _download_guarded,
                track,
                source_url,
                destination,
                output_path,
                quality=quality,
                retries=retries,
                fragment_retries=fragment_retries,
                socket_timeout=socket_timeout,
                cookies_from_browser=cookies_from_browser,
                pot_provider_url=pot_provider_url,
                pot_provider_home=pot_provider_home,
            )
        except TimeoutError as exc:
            _prune_partial(output_path)
            logger.warning(
                "download track=%s url=%s status=failed reason=timeout %.0fs",
                track_identifier(track),
                source_url,
                timeout,
            )
            raise DownloadError(
                f"download timed out after {timeout:.0f}s: {output_path.name}"
            ) from exc
    return _download_guarded(
        track,
        source_url,
        destination,
        output_path,
        quality=quality,
        retries=retries,
        fragment_retries=fragment_retries,
        socket_timeout=socket_timeout,
        cookies_from_browser=cookies_from_browser,
        pot_provider_url=pot_provider_url,
        pot_provider_home=pot_provider_home,
    )


def _download_guarded(
    track: Track,
    source_url: str,
    destination: Path,
    output_path: Path,
    *,
    quality: str,
    retries: int,
    fragment_retries: int,
    socket_timeout: int,
    cookies_from_browser: str | None,
    pot_provider_url: str | None,
    pot_provider_home: str | None,
) -> Path:
    """Run :func:`_download_to` under the per-output-path lock."""
    with _output_lock(output_path):
        return _download_to(
            track,
            source_url,
            destination,
            output_path,
            quality=quality,
            retries=retries,
            fragment_retries=fragment_retries,
            socket_timeout=socket_timeout,
            cookies_from_browser=cookies_from_browser,
            pot_provider_url=pot_provider_url,
            pot_provider_home=pot_provider_home,
        )


def _run_with_timeout(
    timeout: float,
    fn: Callable[..., Path],
    /,
    *args: object,
    **kwargs: object,
) -> Path:
    """Run ``fn`` in a daemon thread and bound its wall-clock execution.

    The worker is a daemon so a genuinely hung yt-dlp/ffmpeg call cannot keep
    the job thread pool alive; the timeout surfaces as a raise here while any
    stuck underlying call finishes or dies on its own in the background.
    """
    outcome: list[Path] = []
    failure: list[BaseException] = []

    def target() -> None:
        try:
            outcome.append(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001 - re-raised for the caller
            failure.append(exc)

    worker = threading.Thread(
        target=target,
        name="spotm3u-download",
        daemon=True,
    )
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"operation exceeded {timeout}s")
    if failure:
        raise failure[0]
    return outcome[0]


def _prune_partial(output_path: Path) -> None:
    """Best-effort removal of a download output that failed or was abandoned."""
    try:
        output_path.unlink(missing_ok=True)
    except OSError:
        pass


def _output_lock(path: Path) -> threading.Lock:
    """Return a lock shared by all callers writing to the same output file."""
    key = str(path.resolve())
    with _OUTPUT_LOCKS_GUARD:
        lock = _OUTPUT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _OUTPUT_LOCKS[key] = lock
        return lock


def _download_to(
    track: Track,
    source_url: str,
    destination: Path,
    output_path: Path,
    *,
    quality: str,
    retries: int,
    fragment_retries: int,
    socket_timeout: int,
    cookies_from_browser: str | None,
    pot_provider_url: str | None,
    pot_provider_home: str | None,
) -> Path:
    output_template = str(output_path.with_suffix(".%(ext)s"))
    try:
        output_path.unlink(missing_ok=True)
    except OSError as exc:
        raise DownloadError(f"cannot replace existing download: {output_path.name}") from exc

    try:
        import yt_dlp  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency is project-managed
        raise DownloadError("yt-dlp is not installed") from exc

    # Load yt-dlp plugins once up front; concurrent lazy loading from download
    # workers re-registers providers ("already registered").
    ensure_ytdlp_plugins_loaded()

    is_youtube = _is_youtube_url(source_url)
    # Never echo credentials embedded in the source URL into logs or errors.
    log_url = _redact_url(source_url) or source_url
    provider_configured = bool(pot_provider_url or pot_provider_home)
    if is_youtube and provider_configured:
        # Fail before yt-dlp starts when an explicitly configured provider is unusable.
        _validate_pot_provider(pot_provider_url, pot_provider_home)

    options = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": quality,
            },
        ],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": retries,
        "fragment_retries": fragment_retries,
        "socket_timeout": socket_timeout,
        "ignoreerrors": False,
        "continuedl": False,
        "overwrites": True,
    }
    if cookies_from_browser:
        options["cookiesfrombrowser"] = (cookies_from_browser.casefold(),)
    if is_youtube:
        # Only YouTube understands the bgutil provider arguments. yt-dlp itself
        # selects and rotates the internal player clients; spotm3u does not
        # blindly retry one client after another.
        extractor_args = _pot_provider_extractor_args(pot_provider_url, pot_provider_home)
        if extractor_args:
            options["extractor_args"] = extractor_args

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            result = ydl.download([source_url])
    except Exception as exc:
        _prune_partial(output_path)
        logger.debug(
            "download track=%s yt-dlp error: %s",
            track_identifier(track),
            _redact_error(_youtube_error_text(exc)),
        )
        if is_youtube:
            classification = _classify_youtube_error(exc)
            message = _youtube_error_message(
                classification,
                cookies_from_browser=cookies_from_browser,
                provider_configured=provider_configured,
            )
        else:
            classification = DOWNLOAD_FAILURE
            message = f"download failed for {log_url}"
        logger.warning(
            "download track=%s url=%s status=failed reason=%s",
            track_identifier(track),
            log_url,
            classification,
        )
        raise DownloadError(message) from exc

    if result not in (None, 0):
        _prune_partial(output_path)
        logger.warning(
            "download track=%s url=%s status=failed reason=exit code %s",
            track_identifier(track),
            log_url,
            result,
        )
        raise DownloadError(f"download failed for {log_url} (exit code {result})")
    if not _is_complete_mp3(output_path, destination):
        _prune_partial(output_path)
        logger.warning(
            "download track=%s url=%s status=failed reason=no complete mp3",
            track_identifier(track),
            log_url,
        )
        raise DownloadError(f"download did not produce a complete MP3: {output_path.name}")
    validation = validate_downloaded_audio(track, output_path)
    if validation.status == "invalid":
        _prune_partial(output_path)
        logger.warning(
            "download track=%s url=%s status=failed reason=audio invalid: %s",
            track_identifier(track),
            log_url,
            ", ".join(validation.reasons),
        )
        raise DownloadError(f"downloaded audio is invalid: {', '.join(validation.reasons)}")
    try:
        result = enrich_metadata(output_path, track, destination)
        if result.errors:
            logger.warning(
                "metadata enrichment track=%s errors=%s",
                track_identifier(track),
                "; ".join(result.errors),
            )
    except MetadataError:
        _prune_partial(output_path)
        raise
    logger.info(
        "download track=%s url=%s status=ok path=%s",
        track_identifier(track),
        log_url,
        output_path,
    )
    return output_path


def download_source(
    source_url: str,
    track: Track,
    output_dir: str | Path,
    *,
    quality: str = "192",
    timeout: float | None = 600,
    cookies_from_browser: str | None = None,
    pot_provider_url: str | None = None,
    pot_provider_home: str | None = None,
) -> Path:
    """Compatibility-oriented source-first wrapper around :func:`download_track`."""
    return download_track(
        track,
        source_url,
        output_dir,
        quality=quality,
        timeout=timeout,
        cookies_from_browser=cookies_from_browser,
        pot_provider_url=pot_provider_url,
        pot_provider_home=pot_provider_home,
    )


def _pot_provider_extractor_args(
    provider_url: str | None,
    provider_home: str | None,
) -> dict[str, dict[str, list[str]]]:
    """Return the bgutil provider arguments for yt-dlp's YouTube extractor."""
    args: dict[str, dict[str, list[str]]] = {}
    if provider_url:
        args["youtubepot-bgutilhttp"] = {"base_url": [provider_url]}
    elif provider_home:
        args["youtubepot-bgutilscript"] = {"server_home": [provider_home]}
    return args


def _youtube_error_text(error: BaseException) -> str:
    """Collect useful text from a yt-dlp error and its chained causes.

    Prefers structured fields (``orig_msg``/``msg``) that yt-dlp extractor and
    download errors carry, then falls back to ``str(error)``. Traversal is
    cycle-safe so a chained ``DownloadError`` cannot loop forever.
    """
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for attribute in ("orig_msg", "msg"):
            value = getattr(current, attribute, None)
            if isinstance(value, str) and value:
                parts.append(value)
        parts.append(str(current))
        current = _chained_error(current)
    return " ".join(parts)


def _chained_error(error: BaseException) -> BaseException | None:
    """Return the exception a yt-dlp error wraps, if any."""
    exc_info = getattr(error, "exc_info", None)
    if (
        isinstance(exc_info, tuple)
        and len(exc_info) == 3
        and isinstance(exc_info[1], BaseException)
    ):
        return exc_info[1]
    return error.__cause__ or error.__context__


def _normalize_message(message: str) -> str:
    """Normalize smart quotes so markers can use plain ASCII."""
    return message.translate(
        str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})
    )


def _classify_youtube_error(error: BaseException) -> str:
    """Classify a YouTube failure into an actionable category.

    The order matters: authentication is checked before content availability so
    that "Sign in to confirm you're not a bot" and "LOGIN_REQUIRED" are treated
    as a session requirement rather than a missing PO token or a generic bot
    block. A plain "bot" substring is never used as a signal.
    """
    message = _normalize_message(_youtube_error_text(error)).casefold()
    if any(marker in message for marker in _PO_TOKEN_MARKERS):
        return PO_TOKEN_UNAVAILABLE
    if any(marker in message for marker in _COOKIE_FAILURE_MARKERS):
        return BROWSER_COOKIE_ERROR
    if any(marker in message for marker in _AUTHENTICATION_MARKERS):
        return AUTHENTICATION_REQUIRED
    if any(marker in message for marker in _RATE_LIMIT_MARKERS):
        return RATE_LIMITED
    if any(marker in message for marker in _CONTENT_UNAVAILABLE_MARKERS):
        return CONTENT_UNAVAILABLE
    return DOWNLOAD_FAILURE


def _youtube_error_message(
    classification: str,
    *,
    cookies_from_browser: str | None,
    provider_configured: bool,
) -> str:
    if classification == PO_TOKEN_UNAVAILABLE:
        return _po_token_message(provider_configured=provider_configured)
    if classification == BROWSER_COOKIE_ERROR:
        return _cookie_required_message(cookies_from_browser, extraction_failed=True)
    if classification == AUTHENTICATION_REQUIRED:
        return _cookie_required_message(cookies_from_browser)
    if classification == RATE_LIMITED:
        return (
            "YouTube rate-limited this request or presented a captcha challenge. "
            "Wait before retrying, lower download.workers, or configure "
            "download.cookies_from_browser to use a signed-in session."
        )
    if classification == CONTENT_UNAVAILABLE:
        return (
            "This YouTube video is not downloadable without an account "
            "(private, members-only, age-restricted, or removed). Sign in to "
            "YouTube in a supported browser and set download.cookies_from_browser "
            "if you have access to it, then retry."
        )
    return "download failed"


def _is_youtube_url(source_url: str) -> bool:
    host = (urlparse(source_url).hostname or "").casefold()
    return host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")


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
        "restart spotm3u; installing the plugin does not start the separate "
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
    with urlopen(Request(ping_url), timeout=5) as response:
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


def _redact_url(url: str | None) -> str | None:
    """Strip credentials embedded in a URL, keeping the rest intact."""
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.username is None and parsed.password is None:
        return url
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return parsed._replace(netloc=host).geturl()


def _redact_error(message: str) -> str:
    """Remove cookies, tokens, credentials, and URL userinfo from a message."""
    if not message:
        return message
    redacted = re.sub(r"(?i)\b(https?://)[^\s/@]+@", r"\1", message)
    redacted = re.sub(r"(?i)\b(cookie\s*[:=]\s*)\S.*", r"\1[redacted]", redacted)
    redacted = re.sub(
        r"(?i)\b(authorization\s*[:=]\s*)(?:bearer\s+)?\S+", r"\1[redacted]", redacted
    )
    redacted = re.sub(
        r"(?i)\b(po_?token|pot_token|token|password|passwd|secret|api_?key)([=:]\s*)\S+",
        r"\1\2[redacted]",
        redacted,
    )
    return redacted


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


def _validate_source_url(source_url: str) -> None:
    if not isinstance(source_url, str) or not source_url.strip():
        raise DownloadError("source URL is required")
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DownloadError("source URL must be an HTTP(S) URL")


def _output_name(track: Track, source_url: str) -> str:
    title = _safe_component(track.title)
    artists = _safe_component(", ".join(track.artists))
    stem = " - ".join(part for part in (title, artists) if part) or _safe_component(source_url)
    return f"{stem}.mp3"


def _safe_component(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip(" -.")
    value = value[:160].strip(" -.")
    if value.casefold().split(".", 1)[0] in {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }:
        value = f"_{value}"
    return value or "_"


def _is_complete_mp3(output_path: Path, destination: Path) -> bool:
    try:
        resolved = output_path.resolve()
        resolved.relative_to(destination)
        return resolved.is_file() and resolved.stat().st_size > 0
    except (OSError, ValueError):
        return False


__all__ = ["DownloadError", "describe_youtube_setup", "download_source", "download_track"]
