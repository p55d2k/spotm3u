# spotm3u

`spotm3u` is a local Python CLI for turning Spotify playlist metadata into
portable `.m3u` playlists. It is designed around the normal Spotify website
and a local music library; it does not use the Spotify Web API as a core
dependency and does not handle Spotify passwords.

The reusable backend foundation includes generic playlist and track models,
text normalization, local audio resolution, and UTF-8 M3U writing. Spotify
browser automation remains isolated under `spotm3u.spotify` for compatibility,
but is not started by the CLI workflow.

## Requirements

- Python 3.13+
- [`uv`](https://docs.astral.sh/uv/)
- Playwright's Chromium browser (installed separately after dependencies)

## Setup

```bash
uv venv
uv sync
uv run playwright install chromium
uv run pre-commit install
```

## Usage

```bash
uv run spotm3u
uv run spotm3u --help
uv run spotm3u --version
```

`spotm3u` currently reports that the reusable backend is available. Browser
session data, when used directly through the isolated Spotify modules, remains
local-only in `.browser-data/` and must not be copied or committed.

The Flask web app can be started with:

```bash
uv run spotm3u-web
```

Open `http://127.0.0.1:5000/` to view the web app homepage.

The installed command can also be used directly after activating the uv
environment:

```bash
source .venv/bin/activate
spotm3u
```

## Configuration

All settings are optional and have built-in defaults. To customize them,
create a `config.toml` in the directory you launch the app from, or point the
`SPOTM3U_CONFIG` environment variable at a file:

```bash
SPOTM3U_CONFIG=~/spotm3u.toml uv run spotm3u-web
```

See `config.toml` in the repo root for a fully commented example. Configurable
areas include the port, music library and download directory, upload size
limit, parallel track resolution, online search result counts, download bitrate
and retries, and M3U formatting (extended header / relative paths).

## Tests

```bash
uv run pytest
```

Commits run the test suite automatically when pre-commit is installed. The
same suite runs in GitHub Actions for pushes and pull requests.

## Project layout

```text
src/spotm3u/       Installable application package
tests/             Automated tests
context.md         Architecture and implementation plan
data_files/        Existing CSV data; intentionally left unchanged
archive/legacy/    Superseded standalone scripts and ExportifyX prototype
```

The files in `archive/legacy/` are retained for reference and are not part
of the new CLI.
