# Development

## Prerequisites

- Python 3.13 or newer
- [`uv`](https://docs.astral.sh/uv/)
- FFmpeg on `PATH` for tests or development flows that perform online audio
  conversion
- Node.js `^20.19.0` or `>=22.12.0` with npm, needed by `uv run dev` and
  `uv run build` (both build or serve the React frontend under `frontend/`);
  the packaged application never needs it

Install the locked development environment from the repository root:

```bash
uv sync --locked --dev
```

Start both development servers with one command:

```bash
uv run dev
```

It runs the Flask application and the Vite development server for the React
frontend side by side, forwarding the output of both to this terminal labelled
per process:

```text
  frontend  http://127.0.0.1:5173/  (proxies /api)
  backend   http://127.0.0.1:5001/
```

Open the frontend address: Vite serves the React app and forwards `/api/*` to
Flask, so the browser never talks to Flask directly and no host or port is
hardcoded in frontend code. Ctrl+C stops both processes, and when either one
exits on its own the other is stopped with it.

Configuration is optional and is read from `./config.toml`, or from the path
in `SPOTM3U_CONFIG`. Both servers bind to loopback (`127.0.0.1`) and prefer
their conventional port (Flask's configured `web.port`, default 5001; Vite's
5173), each falling back to a free port when one is taken. Flask's debug
reloader is enabled, so backend changes are picked up without restarting, and
Vite hot-reloads the frontend; the UI is used in a normal browser with full
developer tools.

The native desktop application is a separate, production-only workflow:

```bash
uv run app
```

`uv run app` starts the same Flask app on a free loopback port, waits for it to
become ready, and then presents the UI inside a native `SpotM3U` window
(pywebview) instead of an external browser; closing the window shuts Flask down
and exits the process. The window is frameless. On Windows and Linux the page
draws its own title bar (the frontend's `TitleBar` component), whose minimize,
maximize and close buttons call a small pywebview JS API; on macOS the native
AppKit traffic lights are restored instead (`desktop_macos.py`), leaving the
page's bar as a transparent drag strip over a full-bleed content view. That same
API (`WindowControls`) carries the two file operations that belong to the
desktop rather than to a web page: the Exportify ZIP is picked in the OS file
dialog (the chosen path stays in the shell and is collected by
`POST /api/upload/picked`), and a generated M3U is saved through the OS save
panel. The window also gets a
persistent WebView storage directory
(`%LOCALAPPDATA%\SpotM3U`,
`~/Library/Application Support/SpotM3U`, or `$XDG_DATA_HOME/spotm3u`;
`SPOTM3U_WEBVIEW_STORAGE` overrides it), so the theme and a dismissed update
notice survive a restart instead of resetting with pywebview's default private
mode. `SPOTM3U_NO_WEBVIEW=1` skips the window and only serves,
which is what the release smoke test uses. Developers are not required to use
the desktop window; it never replaces the plain browser workflow above.

The packaged applications use the same desktop launcher, where the reloader is
disabled and the Windows build is windowed instead of console-based; see
[packaging.md](packaging.md). Build a local distributable with
`uv run build`.

Flask serves two things only: the JSON API under `/api` (`spotm3u/api.py`) and
the built React application at `/` (`spotm3u/frontend.py`). There are no
server-rendered pages. `uv run build` builds the frontend and packs it into the
distributable; see [packaging](packaging.md).

## Frontend

The React + TypeScript frontend lives in `frontend/` and is built with Vite,
Tailwind CSS, and Lucide. `uv run dev` starts it together with Flask: Vite
serves it on <http://127.0.0.1:5173/> and proxies `/api/*` to the backend.

It can also be run on its own against the default Flask port (`web.port`):

```bash
cd frontend
npm install            # once
npm run dev            # Vite dev server only
npm run build          # type-check and emit frontend/dist/
npm run typecheck      # type-check only
```

Requests to Flask use same-origin `/api/...` paths (see
`frontend/src/lib/api.ts` and the endpoint reference in [api.md](api.md)): the
frontend only knows the `/api` namespace and the Vite development proxy
forwards it to Flask unchanged. That namespace is same-origin in production
too, so a production build needs no proxy, no backend address, and no
environment values at build time.

`uv run build` runs `npm ci` and `npm run build` before PyInstaller, so the
distributable always carries the frontend from the checkout being built. A
production build is served by Flask at `/`; see
[packaging](packaging.md#the-react-frontend-in-the-bundle) for what lands in the
bundle and how it is verified.

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
  app.py                 Flask page routes and application setup
  api.py                 `/api` JSON routes used by the React frontend
  frontend.py            locates, builds, and serves the built React application
  web_jobs.py            job, batch, and artwork helpers shared by the routes
  artwork.py             artwork lookup order, embedding, and cleanup
  artwork_sources.py     MusicBrainz/iTunes/Deezer lookups and candidate matching
  artwork_cache.py       artwork cache layout, cache keys, and the in-process memo
  apple_music.py         experimental, opt-in Apple Music catalog id matching
  metadata.py            ID3 metadata read/write and enrichment
  config.py              optional TOML and environment configuration
  desktop.py             native WebView shell used by the packaged application
  dev.py                 ``uv run dev`` supervisor for the Flask and Vite servers
  build.py               ``uv run build`` PyInstaller shortcut
  jobs.py                upload/job lifecycle
  resolution.py          track resolution orchestration
  audio/                 local audio discovery and matching
  exportify/             Exportify ZIP parsing
  online/                search, ranking, downloads, errors, and validation
  m3u/                   playlist writing
frontend/                React + Vite frontend, built and served at /
tests/                   pytest suite, grouped by responsibility (see below)
packaging/               PyInstaller spec and release helpers
docs/                    public project documentation
```

### Test layout

Tests are grouped by what they exercise, not by filename. Add a test to the
directory whose neighbours fail for the same reason:

```text
tests/
  conftest.py            shared offline fixtures and helpers
  unit/                  one component, every external boundary mocked
    matching/            normalization, source search, ranking and validation
    metadata/            ID3 tags, metadata caches and Apple Music matching
    lyrics/              lyrics retrieval, parsing, and frame/sidecar writing
    artwork/             album and artist artwork resolution and embedding
    downloads/           downloader, download cache, audio validation, FFmpeg
    library/             local audio discovery, Exportify parsing, M3U writing
    media_player/        the platform "Add to Media Player" integration
    uploads/             ZIP uploads and upload-directory cleanup
    utilities/           models, configuration, and logging
  integration/           several components driven together
    processing/          resolution, jobs, fast mode, and the full pipeline
    api/                 the Flask app and its `/api` routes
    frontend/            locating, building, and serving the React build
    desktop/             native window shell and the production launcher
  regression/            pinned bugs and pipeline decisions that must not change
    matching/            the matching regression suite and the live (opt-in) checks
  packaging/             release helpers: build, icons, bundles, smoke test
```

Unit tests must stay runnable offline: the root `conftest.py` already stubs
artwork and lyrics providers, and a test that needs a real provider belongs
behind the `network` marker (see `regression/matching`) instead.

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
