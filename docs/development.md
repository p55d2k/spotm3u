# Development

## Prerequisites

- Python 3.13 or newer
- [`uv`](https://docs.astral.sh/uv/)
- FFmpeg on `PATH` for tests or development flows that perform online audio
  conversion

Install the locked development environment from the repository root:

```bash
uv sync --locked --dev
```

Start the development server with:

```bash
uv run dev
```

Configuration is optional and is read from `./config.toml`, or from the path
in `SPOTM3U_CONFIG`. The development server binds to loopback (`127.0.0.1`)
and prefers the configured `web.port` (default 5001), falling back to a free
port when that one is taken. Flask's debug reloader is enabled, so frontend and
backend changes are picked up without rebuilding anything; the UI is used in a
normal browser with full developer tools.

The native desktop application is a separate, production-only workflow:

```bash
uv run app
```

`uv run app` starts the same Flask app on a free loopback port, waits for it to
become ready, and then presents the UI inside a native `SpotM3U` window
(pywebview) instead of an external browser; closing the window shuts Flask down
and exits the process. The window is frameless. On Windows and Linux the page
draws its own title bar (`templates/_titlebar.html`), whose minimize, maximize
and close buttons call a small pywebview JS API; on macOS the native AppKit
traffic lights are restored instead (`desktop_macos.py`), leaving the page's bar
as a transparent drag strip over a full-bleed content view. `SPOTM3U_NO_WEBVIEW=1`
skips the window and only serves,
which is what the release smoke test uses. Developers are not required to use
the desktop window; it never replaces the plain browser workflow above.

The packaged applications use the same desktop launcher, where the reloader is
disabled and the Windows build is windowed instead of console-based; see
[packaging.md](packaging.md). Build a local distributable with
`uv run build`.

## Checks

```bash
uv run pytest
uv run ruff check src tests packaging
uv run ruff format --check src tests packaging
uv run pre-commit run --all-files
```

Install the pre-commit hook once per clone:

```bash
uv run pre-commit install
```

The CI workflow runs Ruff and pytest on pushes and pull requests. The local
pre-commit configuration also runs the full test suite.

## Project layout

```text
src/spotm3u/
  app.py                 Flask routes and application setup
  web_jobs.py            job, batch, and artwork helpers used by the Flask routes
  artwork.py             artwork lookup order, embedding, and cleanup
  artwork_sources.py     MusicBrainz/iTunes/Deezer lookups and candidate matching
  artwork_cache.py       artwork cache layout, cache keys, and the in-process memo
  metadata.py            ID3 metadata read/write and enrichment
  config.py              optional TOML and environment configuration
  desktop.py             native WebView shell used by the packaged application
  dev.py                 ``uv run dev`` development server
  build.py               ``uv run build`` PyInstaller shortcut
  jobs.py                upload/job lifecycle
  resolution.py          track resolution orchestration
  audio/                 local audio discovery and matching
  exportify/             Exportify ZIP parsing
  online/                search, ranking, downloads, errors, and validation
  m3u/                   playlist writing
  templates/             Jinja templates
  static/                CSS
tests/                   pytest suite
packaging/               PyInstaller spec and release helpers
docs/                    public project documentation
```

Keep Flask routes thin and put reusable behavior in the relevant package.
Exportify-specific parsing should not leak CSV details into generic playlist,
matching, or M3U code. Add tests for non-trivial behavior and preserve
playlist order and intentional duplicates.

## Configuration

Use `config.toml` as the starting point for local settings. Do not commit
credentials, browser cookie databases, downloaded media, or machine-specific
paths. `SPOTM3U_LOG_LEVEL=DEBUG` enables detailed diagnostic logging without
changing the default log level.

If a UI change affects the screenshots embedded in the README or
[web.md](web.md), recapture the affected PNGs in `docs/images/` from the
current application.
