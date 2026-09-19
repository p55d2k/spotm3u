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
uv run spotm3u
```

The server listens on `127.0.0.1:5001`. Configuration is optional and is read
from `./config.toml`, or from the path in `SPOTM3U_CONFIG`.

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
  config.py              optional TOML and environment configuration
  jobs.py                upload/job lifecycle
  resolution.py          track resolution orchestration
  audio/                 local audio discovery and matching
  exportify/             Exportify ZIP parsing
  online/                search, ranking, downloads, and validation
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

If UI changes affect a documented state, update the
[screenshot plan](screenshots.md) and recapture the affected PNGs from the
current application.
