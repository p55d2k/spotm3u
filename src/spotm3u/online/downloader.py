"""Download validated online sources into a controlled MP3 directory."""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from ..log import track_identifier
from ..metadata import MetadataError, enrich_metadata
from ..models import Track
from ._ytdlp import ensure_ytdlp_plugins_loaded
from .audio_validation import validate_downloaded_audio
from .errors import (
    AUTHENTICATION_REQUIRED,
    BROWSER_COOKIE_ERROR,
    CONTENT_UNAVAILABLE,
    DOWNLOAD_FAILURE,
    PO_TOKEN_UNAVAILABLE,
    RATE_LIMITED,
    DownloadError,
    _redact_error,
    _redact_url,
)
from .youtube_setup import (
    _cookie_required_message,
    _po_token_message,
    _validate_pot_provider,
)

logger = logging.getLogger(__name__)

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
    ffmpeg_location: str | None = None,
    verify: bool = True,
) -> Path:
    """Download ``source_url`` and return its local MP3 path.

    ``output_dir`` is the caller-owned job/library directory. All yt-dlp
    intermediate files and the final file are constrained to that directory.

    ``timeout`` bounds the entire download wall-clock time so a hung yt-dlp or
    ffmpeg run cannot occupy a worker forever. It is raised as a
    :class:`DownloadError`; ``socket_timeout`` remains the network-level
    fallback that bounds each socket operation.

    ``ffmpeg_location``, when given, is passed to yt-dlp unchanged so a bundled
    or otherwise non-``PATH`` FFmpeg is found for audio extraction.

    ``verify`` controls the expensive post-download steps. When ``False`` the
    download stops once a complete MP3 exists: no audio validation and no
    metadata enrichment run, and a file produced by a concurrent download of
    the same track is reused without re-validating it. This is the audio-only
    path fast mode uses; it never changes what yt-dlp itself downloads.
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
        ffmpeg_location=ffmpeg_location,
        verify=verify,
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
    ffmpeg_location: str | None,
    verify: bool,
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
                    ffmpeg_location=ffmpeg_location,
                    verify=verify,
                ),
            )

        reused = _await_peer(in_flight, track, timeout, verify=verify)
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
    *,
    verify: bool = True,
) -> Path | None:
    """Wait for the owner of ``in_flight`` and reuse its result.

    Returns the owner's path when it produced a file that still passes audio
    validation against ``track``; ``None`` means the caller must download the
    file itself. With ``verify`` disabled (fast mode) a complete file is
    reused as-is, matching the download that produced it.
    """
    with in_flight.condition:
        in_flight.condition.wait_for(lambda: in_flight.done, timeout=timeout)
        if in_flight.done and in_flight.path is not None and in_flight.path.is_file():
            if not verify or validate_downloaded_audio(track, in_flight.path).status == "valid":
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
    ffmpeg_location: str | None,
    verify: bool,
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
                ffmpeg_location=ffmpeg_location,
                verify=verify,
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
        ffmpeg_location=ffmpeg_location,
        verify=verify,
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
    ffmpeg_location: str | None,
    verify: bool,
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
            ffmpeg_location=ffmpeg_location,
            verify=verify,
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
    ffmpeg_location: str | None,
    verify: bool = True,
) -> Path:
    """Download one source into ``output_path``.

    With ``verify`` disabled the file is complete as soon as yt-dlp and the
    FFmpeg extraction succeed: audio validation and metadata enrichment are
    skipped (fast mode).
    """
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
    if ffmpeg_location:
        options["ffmpeg_location"] = ffmpeg_location
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
    if verify:
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


__all__ = ["DownloadError", "download_source", "download_track"]
