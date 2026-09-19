import sys
import time
import types
from pathlib import Path
from urllib.error import URLError

import pytest

from spotm3u.models import Track
from spotm3u.online import DownloadError, download_track, downloader

TRACK = Track(title="Song / Name", artists=["An Artist"], spotify_id="track-1")
YOUTUBE_URL = "https://youtube.com/watch?v=example"


class FakeYoutubeDL:
    options: dict = {}
    result = 0
    writes_mp3 = True

    def __init__(self, options: dict) -> None:
        type(self).options = options

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def download(self, urls):
        if self.writes_mp3:
            output = Path(self.options["outtmpl"].replace("%(ext)s", "mp3"))
            output.write_bytes(b"valid mp3")
        return self.result


def _noop_validate_provider(_url, _home):
    return None


def _valid_audio(_track, _path):
    return types.SimpleNamespace(status="valid", reasons=())


def install_fake_yt_dlp(monkeypatch, fake=FakeYoutubeDL, *, validate_provider=None):
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=fake))
    validate_provider = validate_provider or _noop_validate_provider
    monkeypatch.setattr("spotm3u.online.downloader._validate_pot_provider", validate_provider)
    monkeypatch.setattr(
        "spotm3u.online.downloader.validate_downloaded_audio",
        lambda track, path: types.SimpleNamespace(status="valid", reasons=()),
    )
    monkeypatch.setattr(
        "spotm3u.online.downloader.enrich_metadata",
        lambda path, track, download_dir: types.SimpleNamespace(
            artwork_embedded=False,
            artwork_source=None,
            fields_written=("TIT2", "TPE1", "TALB", "TPE2"),
            errors=(),
        ),
    )


# --- Normal (non-YouTube) behavior -----------------------------------------


def test_download_produces_safe_mp3_inside_output_directory(tmp_path, monkeypatch):
    install_fake_yt_dlp(monkeypatch)

    result = download_track(TRACK, "https://example.com/source", tmp_path)

    assert result.parent == tmp_path.resolve()
    assert result.suffix == ".mp3"
    assert result.exists()
    assert "/" not in result.stem
    assert "FFmpegExtractAudio" in FakeYoutubeDL.options["postprocessors"][0]["key"]


def test_non_youtube_download_does_not_use_youtube_provider_behavior(tmp_path, monkeypatch):
    checks = []

    def validate_provider(url, home):
        checks.append((url, home))

    install_fake_yt_dlp(monkeypatch, validate_provider=validate_provider)

    result = download_track(
        TRACK,
        "https://example.com/source",
        tmp_path,
        cookies_from_browser="chrome",
        pot_provider_url="http://127.0.0.1:4416",
    )

    assert result.exists()
    # Provider preflight and YouTube-specific extractor args are YouTube-only.
    assert checks == []
    assert "extractor_args" not in FakeYoutubeDL.options
    # The browser option is generic and still forwarded.
    assert FakeYoutubeDL.options["cookiesfrombrowser"] == ("chrome",)


def test_download_name_follows_spotify_convention(tmp_path, monkeypatch):
    install_fake_yt_dlp(monkeypatch)
    track = Track(title="Wonderwall (Remastered)", artists=["Oasis"])

    result = download_track(track, "https://example.com/source", tmp_path)

    assert result.name == "Wonderwall (Remastered) - Oasis.mp3"


def test_download_embeds_spotify_metadata(tmp_path, monkeypatch):
    install_fake_yt_dlp(monkeypatch)
    track = Track(
        title="Wonderwall (Remastered)",
        artists=["Oasis"],
        album="(What's the Story) Morning Glory?",
    )

    result = download_track(track, "https://example.com/source", tmp_path)

    # The actual metadata writing is tested in test_metadata.py
    assert result.suffix == ".mp3"
    assert "Wonderwall" in result.name
    assert "Oasis" in result.name


def test_same_track_and_source_have_deterministic_name(tmp_path, monkeypatch):
    install_fake_yt_dlp(monkeypatch)
    first = download_track(TRACK, "https://example.com/source", tmp_path)
    first.unlink()
    second = download_track(TRACK, "https://example.com/source", tmp_path)
    assert first == second


def test_invalid_url_is_rejected_without_downloader(tmp_path):
    with pytest.raises(DownloadError, match="HTTP"):
        download_track(TRACK, "not a URL", tmp_path)


def test_downloader_failure_is_explicit(tmp_path, monkeypatch):
    class Failing(FakeYoutubeDL):
        result = 1

    install_fake_yt_dlp(monkeypatch, Failing)
    with pytest.raises(DownloadError, match="download failed"):
        download_track(TRACK, "https://example.com/source", tmp_path)


def test_missing_output_is_not_success(tmp_path, monkeypatch):
    class NoOutput(FakeYoutubeDL):
        writes_mp3 = False

    install_fake_yt_dlp(monkeypatch, NoOutput)
    with pytest.raises(DownloadError, match="complete MP3"):
        download_track(TRACK, "https://example.com/source", tmp_path)


def test_invalid_download_is_discarded(tmp_path, monkeypatch):
    class Fake(FakeYoutubeDL):
        def download(self, urls):
            output = Path(self.options["outtmpl"].replace("%(ext)s", "mp3"))
            output.write_bytes(b"not music")
            return 0

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=Fake))
    monkeypatch.setattr(
        "spotm3u.online.downloader.validate_downloaded_audio",
        lambda track, path: types.SimpleNamespace(status="invalid", reasons=("speech detected",)),
    )

    with pytest.raises(DownloadError, match="invalid"):
        download_track(TRACK, "https://example.com/source", tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_hung_download_is_abandoned_after_wall_clock_timeout(tmp_path, monkeypatch):
    class Hung(FakeYoutubeDL):
        def download(self, urls):
            time.sleep(2)
            return 0

    install_fake_yt_dlp(monkeypatch, Hung)

    with pytest.raises(DownloadError, match="timed out"):
        download_track(TRACK, "https://example.com/source", tmp_path, timeout=0.2)


# --- Concurrent deduplication -------------------------------------------------


def test_concurrent_downloads_download_shared_output_once(tmp_path, monkeypatch):
    """Parallel jobs needing the same file wait for it instead of re-downloading."""
    import threading

    gate = threading.Event()
    first_started = threading.Event()
    waiter_entered = threading.Event()
    calls: list[Path] = []

    def fake_perform(track, source_url, destination, output_path, **kwargs):
        calls.append(output_path)
        first_started.set()
        gate.wait(timeout=5)
        output_path.write_bytes(b"mp3")
        return output_path

    real_await_peer = downloader._await_peer

    def wrapped_await_peer(in_flight, track, timeout):
        waiter_entered.set()
        return real_await_peer(in_flight, track, timeout)

    monkeypatch.setattr(downloader, "_perform_download", fake_perform)
    monkeypatch.setattr(downloader, "validate_downloaded_audio", _valid_audio)
    monkeypatch.setattr(downloader, "_await_peer", wrapped_await_peer)

    results: list[Path] = []
    errors: list[BaseException] = []

    def run():
        try:
            results.append(download_track(TRACK, YOUTUBE_URL, tmp_path))
        except BaseException as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    assert first_started.wait(timeout=5)
    assert waiter_entered.wait(timeout=5), "the second caller must wait for the owner"
    gate.set()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert len(calls) == 1, "a shared output must be downloaded only once"
    assert len(results) == 2
    assert results[0] == results[1]


def test_waiting_caller_downloads_itself_after_owner_fails(tmp_path, monkeypatch):
    import threading

    first = True

    def fake_perform(track, source_url, destination, output_path, **kwargs):
        nonlocal first
        if first:
            first = False
            raise DownloadError("peer failed")
        output_path.write_bytes(b"mp3")
        return output_path

    monkeypatch.setattr(downloader, "_perform_download", fake_perform)
    monkeypatch.setattr(downloader, "validate_downloaded_audio", _valid_audio)

    results: list[Path] = []
    errors: list[BaseException] = []

    def run():
        try:
            results.append(download_track(TRACK, YOUTUBE_URL, tmp_path))
        except BaseException as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(errors) == 1
    assert "peer failed" in str(errors[0])
    assert len(results) == 1
    assert results[0].is_file()


def test_peer_result_is_not_reused_when_it_is_invalid_for_the_waiting_track(tmp_path, monkeypatch):
    """A different recording sharing an output name must not inherit a peer's file."""
    import threading

    gate = threading.Event()
    first_started = threading.Event()
    calls: list[str] = []

    def fake_perform(track, source_url, destination, output_path, **kwargs):
        calls.append(track.spotify_id)
        first_started.set()
        gate.wait(timeout=5)
        output_path.write_bytes(b"mp3")
        return output_path

    def fake_validate(track, path):
        return types.SimpleNamespace(
            status="valid" if track.spotify_id == "shared-good" else "invalid",
            reasons=(),
        )

    monkeypatch.setattr(downloader, "_perform_download", fake_perform)
    monkeypatch.setattr(downloader, "validate_downloaded_audio", fake_validate)

    good = Track(title="Shared Song", artists=["Artist"], spotify_id="shared-good")
    other = Track(title="Shared Song", artists=["Artist"], spotify_id="shared-other")
    results: list[Path] = []
    errors: list[BaseException] = []

    def run(track):
        try:
            results.append(download_track(track, YOUTUBE_URL, tmp_path))
        except BaseException as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=(good,)),
        threading.Thread(target=run, args=(other,)),
    ]
    for thread in threads:
        thread.start()
    assert first_started.wait(timeout=5)
    time.sleep(0.1)
    gate.set()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert set(calls) == {"shared-good", "shared-other"}
    assert len(results) == 2


# --- YouTube configuration --------------------------------------------------


def test_youtube_passes_browser_cookies_to_yt_dlp(tmp_path, monkeypatch):
    install_fake_yt_dlp(monkeypatch)

    download_track(TRACK, YOUTUBE_URL, tmp_path, cookies_from_browser="chrome")

    assert FakeYoutubeDL.options["cookiesfrombrowser"] == ("chrome",)


def test_youtube_passes_custom_pot_provider_to_yt_dlp(tmp_path, monkeypatch):
    install_fake_yt_dlp(monkeypatch)

    download_track(
        TRACK,
        YOUTUBE_URL,
        tmp_path,
        pot_provider_url="http://127.0.0.1:8080",
    )

    assert FakeYoutubeDL.options["extractor_args"] == {
        "youtubepot-bgutilhttp": {"base_url": ["http://127.0.0.1:8080"]}
    }


def test_youtube_passes_custom_pot_script_to_yt_dlp(tmp_path, monkeypatch):
    install_fake_yt_dlp(monkeypatch)

    download_track(
        TRACK,
        YOUTUBE_URL,
        tmp_path,
        pot_provider_home="/tmp/bgutil/server",
    )

    assert FakeYoutubeDL.options["extractor_args"] == {
        "youtubepot-bgutilscript": {"server_home": ["/tmp/bgutil/server"]}
    }


def test_configured_http_provider_is_checked_before_yt_dlp_starts(tmp_path, monkeypatch):
    order = []

    def validate_provider(url, home):
        order.append(("validate", url, home))

    class Recorder(FakeYoutubeDL):
        def __init__(self, options):
            order.append(("download", None, None))
            super().__init__(options)

    install_fake_yt_dlp(monkeypatch, Recorder, validate_provider=validate_provider)

    download_track(
        TRACK,
        YOUTUBE_URL,
        tmp_path,
        pot_provider_url="http://127.0.0.1:4416",
    )

    assert order == [
        ("validate", "http://127.0.0.1:4416", None),
        ("download", None, None),
    ]


def test_youtube_without_provider_skips_provider_preflight(tmp_path, monkeypatch):
    checks = []
    install_fake_yt_dlp(monkeypatch, validate_provider=lambda url, home: checks.append((url, home)))

    download_track(TRACK, YOUTUBE_URL, tmp_path)

    assert checks == []


# --- YouTube failure classification ----------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "[youtube] ES5nF0bvCtc: Sign in to confirm you're not a bot. "
        "Use --cookies-from-browser or --cookies for the authentication.",
        "[youtube] ES5nF0bvCtc: LOGIN_REQUIRED",
        "[youtube] ES5nF0bvCtc: Sign in to confirm you\u2019re not a bot.",
        "This video is only available for registered users",
    ],
)
def test_youtube_authentication_required_is_not_a_bot_retry(message, tmp_path, monkeypatch):
    invocations = []

    class LoginRequired(FakeYoutubeDL):
        def __init__(self, options):
            super().__init__(options)
            invocations.append(options)

        def download(self, urls):
            raise RuntimeError(message)

    install_fake_yt_dlp(monkeypatch, LoginRequired)

    with pytest.raises(DownloadError, match="signed-in browser session"):
        download_track(TRACK, YOUTUBE_URL, tmp_path)

    # Authentication is a session problem, not something a client retry fixes.
    assert len(invocations) == 1
    assert "youtube" not in invocations[0].get("extractor_args", {})


def test_youtube_authentication_required_with_browser_points_at_cookie_load(tmp_path, monkeypatch):
    class LoginRequired(FakeYoutubeDL):
        def download(self, urls):
            raise RuntimeError("LOGIN_REQUIRED: Sign in to confirm you're not a bot")

    install_fake_yt_dlp(monkeypatch, LoginRequired)

    with pytest.raises(DownloadError, match="cookies"):
        download_track(TRACK, YOUTUBE_URL, tmp_path, cookies_from_browser="chrome")


@pytest.mark.parametrize(
    "message",
    [
        'Error fetching PO Token from "bgutil:http" provider: '
        "PoTokenProviderError('Error reaching POST /get_pot')",
        "PO Token provider is not available",
        "bgutil:http server is not available",
    ],
)
def test_youtube_po_token_failure_is_not_browser_authentication(message, tmp_path, monkeypatch):
    class PotFailure(FakeYoutubeDL):
        def download(self, urls):
            raise RuntimeError(message)

    install_fake_yt_dlp(monkeypatch, PotFailure)

    with pytest.raises(DownloadError, match="PO Token provider"):
        download_track(
            TRACK,
            YOUTUBE_URL,
            tmp_path,
            pot_provider_url="http://127.0.0.1:4416",
        )


@pytest.mark.parametrize(
    "message",
    [
        "ERROR: Could not copy Chrome cookie database. "
        "See  https://github.com/yt-dlp/yt-dlp/issues/7271  for more info",
        "failed to decrypt cookie (AES-CBC) because UTF-8 decoding failed",
        'could not find chrome cookies database in "/home/x/.config/google-chrome"',
        "failed to load cookies",
    ],
)
def test_youtube_browser_cookie_extraction_failure_reports_setup(message, tmp_path, monkeypatch):
    class CookieFailure(FakeYoutubeDL):
        def download(self, urls):
            raise RuntimeError(message)

    install_fake_yt_dlp(monkeypatch, CookieFailure)

    with pytest.raises(DownloadError, match="cookies could not be read"):
        download_track(TRACK, YOUTUBE_URL, tmp_path, cookies_from_browser="chrome")


@pytest.mark.parametrize(
    "message",
    [
        "This content isn't available, try again later. The current session has "
        "been rate-limited by YouTube for up to an hour.",
        "All player responses are invalid. Your IP is likely being blocked by Youtube",
        "YouTube is requiring a captcha challenge before playback",
        "HTTP Error 429: Too Many Requests",
    ],
)
def test_youtube_rate_limit_is_distinct_from_authentication(message, tmp_path, monkeypatch):
    class RateLimited(FakeYoutubeDL):
        def download(self, urls):
            raise RuntimeError(message)

    install_fake_yt_dlp(monkeypatch, RateLimited)

    with pytest.raises(DownloadError, match="rate-limited this request"):
        download_track(TRACK, YOUTUBE_URL, tmp_path)


def test_youtube_failure_triggers_exactly_one_yt_dlp_invocation(tmp_path, monkeypatch):
    invocations = []

    class AlwaysFails(FakeYoutubeDL):
        def __init__(self, options):
            super().__init__(options)
            invocations.append(options)

        def download(self, urls):
            raise RuntimeError("Sign in to confirm you're not a bot")

    install_fake_yt_dlp(monkeypatch, AlwaysFails)

    with pytest.raises(DownloadError):
        download_track(TRACK, YOUTUBE_URL, tmp_path)

    assert len(invocations) == 1
    assert invocations[0].get("extractor_args", {}) == {}


def test_youtube_generic_failure_is_not_misclassified(tmp_path, monkeypatch):
    class NetworkFailure(FakeYoutubeDL):
        def download(self, urls):
            raise RuntimeError("Unable to download video data: connection reset by peer")

    install_fake_yt_dlp(monkeypatch, NetworkFailure)

    with pytest.raises(DownloadError, match="download failed"):
        download_track(TRACK, YOUTUBE_URL, tmp_path)


@pytest.mark.parametrize(
    ("message", "classification"),
    [
        (
            "[youtube] ES5nF0bvCtc: Sign in to confirm you're not a bot. "
            "Use --cookies-from-browser or --cookies for the authentication.",
            downloader.AUTHENTICATION_REQUIRED,
        ),
        ("LOGIN_REQUIRED", downloader.AUTHENTICATION_REQUIRED),
        (
            'Error fetching PO Token from "bgutil:http" provider: oops',
            downloader.PO_TOKEN_UNAVAILABLE,
        ),
        ("Could not copy Chrome cookie database", downloader.BROWSER_COOKIE_ERROR),
        ("failed to decrypt cookie (AES-GCM)", downloader.BROWSER_COOKIE_ERROR),
        (
            "Private video. Sign in if you've been granted access to this video",
            downloader.CONTENT_UNAVAILABLE,
        ),
        (
            "The current session has been rate-limited by YouTube for up to an hour",
            downloader.RATE_LIMITED,
        ),
        (
            "Your IP is likely being blocked by Youtube",
            downloader.RATE_LIMITED,
        ),
        ("network connection failed", downloader.DOWNLOAD_FAILURE),
    ],
)
def test_youtube_error_classification(message, classification):
    assert downloader._classify_youtube_error(RuntimeError(message)) == classification


def test_classifier_prefers_structured_extractor_message():
    class FakeExtractorError(Exception):
        def __init__(self, orig_msg):
            super().__init__("wrapped generic message")
            self.orig_msg = orig_msg
            self.video_id = "abc"

    error = FakeExtractorError("Sign in to confirm you're not a bot. LOGIN_REQUIRED")

    assert downloader._classify_youtube_error(error) == downloader.AUTHENTICATION_REQUIRED


def test_authentication_and_po_token_are_distinct_categories():
    auth = RuntimeError("LOGIN_REQUIRED: Sign in to confirm you're not a bot")
    pot = RuntimeError("PO Token provider unavailable")

    assert downloader._classify_youtube_error(auth) == downloader.AUTHENTICATION_REQUIRED
    assert downloader._classify_youtube_error(pot) == downloader.PO_TOKEN_UNAVAILABLE


# --- Provider validation ----------------------------------------------------


class _Response:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._payload


def test_http_provider_ping_is_validated_before_yt_dlp(monkeypatch):
    monkeypatch.setattr(downloader, "find_spec", lambda name: object())
    monkeypatch.setattr(downloader, "version", lambda name: "2.0.0")
    monkeypatch.setattr(
        downloader, "urlopen", lambda request, timeout: _Response(b'{"version": "2.0.0"}')
    )

    downloader._validate_pot_provider("http://127.0.0.1:4416", None)


def test_http_provider_minor_version_difference_is_allowed(monkeypatch):
    monkeypatch.setattr(downloader, "find_spec", lambda name: object())
    monkeypatch.setattr(downloader, "version", lambda name: "2.0.0")
    monkeypatch.setattr(
        downloader, "urlopen", lambda request, timeout: _Response(b'{"version": "2.1.3"}')
    )

    downloader._validate_pot_provider("http://127.0.0.1:4416", None)


def test_http_provider_major_version_mismatch_is_a_setup_error(monkeypatch):
    monkeypatch.setattr(downloader, "find_spec", lambda name: object())
    monkeypatch.setattr(downloader, "version", lambda name: "2.0.0")
    monkeypatch.setattr(
        downloader, "urlopen", lambda request, timeout: _Response(b'{"version": "1.9.0"}')
    )

    with pytest.raises(DownloadError, match="incompatible"):
        downloader._validate_pot_provider("http://127.0.0.1:4416", None)


def test_unreachable_http_provider_is_reported_without_leaking_url(monkeypatch):
    monkeypatch.setattr(downloader, "find_spec", lambda name: object())

    def boom(request, timeout):
        raise URLError("offline")

    monkeypatch.setattr(downloader, "urlopen", boom)

    with pytest.raises(DownloadError, match="not running or reachable") as failure:
        downloader._validate_pot_provider("http://user:secret@127.0.0.1:4416", None)
    assert "secret" not in str(failure.value)


def test_invalid_http_provider_url_is_rejected(monkeypatch):
    monkeypatch.setattr(downloader, "find_spec", lambda name: object())

    with pytest.raises(DownloadError, match="invalid"):
        downloader._validate_pot_provider("ftp://127.0.0.1", None)


def test_missing_bgutil_plugin_is_a_setup_error(monkeypatch):
    monkeypatch.setattr(downloader, "find_spec", lambda name: None)

    with pytest.raises(DownloadError, match="plugin is not installed"):
        downloader._validate_pot_provider("http://127.0.0.1:4416", None)


def test_script_provider_requires_built_script_and_runtime(tmp_path, monkeypatch):
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "generate_once.js").write_text("", encoding="utf-8")
    monkeypatch.setattr(downloader, "find_spec", lambda name: object())
    monkeypatch.setattr(downloader.shutil, "which", lambda name: "/usr/bin/" + name)

    downloader._validate_pot_provider(None, str(tmp_path))


def test_script_provider_without_built_script_is_a_setup_error(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "find_spec", lambda name: object())
    monkeypatch.setattr(downloader.shutil, "which", lambda name: "/usr/bin/" + name)

    with pytest.raises(DownloadError, match="not usable"):
        downloader._validate_pot_provider(None, str(tmp_path))


def test_script_provider_does_not_run_arbitrary_paths(tmp_path, monkeypatch):
    """Validation must only look for the expected built artifacts."""
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "generate_once.js").write_text("", encoding="utf-8")
    seen = []

    def fake_which(name):
        seen.append(name)
        return "/usr/bin/" + name

    monkeypatch.setattr(downloader, "find_spec", lambda name: object())
    monkeypatch.setattr(downloader.shutil, "which", fake_which)

    downloader._validate_pot_provider(None, str(tmp_path))

    assert set(seen) <= {"node", "deno"}


# --- Diagnostics helper -----------------------------------------------------


def test_describe_youtube_setup_reports_healthy_provider(monkeypatch):
    monkeypatch.setattr(downloader, "find_spec", lambda name: object())
    monkeypatch.setattr(downloader, "version", lambda name: "2.0.0")
    monkeypatch.setattr(
        downloader, "urlopen", lambda request, timeout: _Response(b'{"version": "2.0.0"}')
    )

    report = downloader.describe_youtube_setup(
        cookies_from_browser="chrome",
        pot_provider_url="http://user:secret@127.0.0.1:4416",
    )

    assert report["bgutil_plugin_installed"] is True
    assert report["bgutil_plugin_version"] == "2.0.0"
    assert report["cookies_from_browser"] == "chrome"
    assert report["http_provider"]["reachable"] is True
    assert report["http_provider"]["compatible"] is True
    assert report["pot_provider_url"] == "http://127.0.0.1:4416"
    assert "secret" not in repr(report)
    assert "po_token" not in repr(report).casefold()


def test_describe_youtube_setup_reports_unreachable_provider_without_raising(monkeypatch):
    monkeypatch.setattr(downloader, "find_spec", lambda name: object())

    def boom(request, timeout):
        raise URLError("offline")

    monkeypatch.setattr(downloader, "urlopen", boom)

    report = downloader.describe_youtube_setup(pot_provider_url="http://user:secret@127.0.0.1:4416")

    assert report["http_provider"]["reachable"] is False
    assert "secret" not in repr(report)


def test_describe_youtube_setup_reports_script_provider(tmp_path, monkeypatch):
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "generate_once.js").write_text("", encoding="utf-8")
    monkeypatch.setattr(downloader, "find_spec", lambda name: object())
    monkeypatch.setattr(downloader.shutil, "which", lambda name: "/usr/bin/" + name)

    report = downloader.describe_youtube_setup(pot_provider_home=str(tmp_path))

    assert report["script_provider"]["available"] is True
    assert report["script_provider"]["runtime"] == "node"


def test_describe_youtube_setup_notes_missing_browser(monkeypatch):
    monkeypatch.setattr(downloader, "find_spec", lambda name: object())

    report = downloader.describe_youtube_setup()

    assert report["cookies_from_browser"] is None
    assert any("cookies_from_browser" in note for note in report["notes"])


# --- yt-dlp plugin load race ------------------------------------------------


def test_plugins_load_once_under_concurrent_downloads(monkeypatch):
    import threading

    loads = []

    class _Flag:
        value = False

    flag = _Flag()

    def fake_load_all_plugins():
        loads.append(1)
        time.sleep(0.05)
        flag.value = True

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace())
    monkeypatch.setitem(
        sys.modules, "yt_dlp.globals", types.SimpleNamespace(all_plugins_loaded=flag)
    )
    monkeypatch.setitem(
        sys.modules,
        "yt_dlp.plugins",
        types.SimpleNamespace(load_all_plugins=fake_load_all_plugins),
    )

    from spotm3u.online import _ytdlp

    threads = [threading.Thread(target=_ytdlp.ensure_ytdlp_plugins_loaded) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert loads == [1]


# --- Secret redaction -------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "leaked"),
    [
        ("Cookie: session=abcdef; other=1", "abcdef"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9", "eyJhbGciOiJIUzI1NiJ9"),
        ("po_token=abc123", "abc123"),
        ("password: hunter2", "hunter2"),
        ("https://user:secret@example.com/path", "secret@"),
    ],
)
def test_redact_error_hides_secrets(message, leaked):
    redacted = downloader._redact_error(message)

    assert leaked not in redacted


def test_source_url_credentials_are_not_echoed_in_errors(tmp_path, monkeypatch):
    class Failing(FakeYoutubeDL):
        result = 1

    install_fake_yt_dlp(monkeypatch, Failing)

    with pytest.raises(DownloadError) as failure:
        download_track(TRACK, "https://user:secret@example.com/source", tmp_path)

    assert "secret" not in str(failure.value)


def test_download_error_does_not_leak_cookie_values_from_ytdlp(tmp_path, monkeypatch):
    class CookieFailure(FakeYoutubeDL):
        def download(self, urls):
            raise RuntimeError(
                "ERROR: Could not copy Chrome cookie database. "
                "Cookie: SID=super-secret-session-value"
            )

    install_fake_yt_dlp(monkeypatch, CookieFailure)

    with pytest.raises(DownloadError) as failure:
        download_track(TRACK, YOUTUBE_URL, tmp_path, cookies_from_browser="chrome")

    assert "super-secret-session-value" not in str(failure.value)
