# spotm3u

`spotm3u` is a local Python CLI for turning Spotify playlist metadata into
portable `.m3u` playlists. It is designed around the normal Spotify website
and a local music library; it does not use the Spotify Web API as a core
dependency and does not handle Spotify passwords.

The project is currently at **Task 1: project setup**. The CLI and data model
foundation are in place; browser authentication, playlist extraction, local
audio matching, and M3U writing will be added in later tasks.

## Requirements

- Python 3.13+
- [`uv`](https://docs.astral.sh/uv/)
- Playwright's Chromium browser (installed separately after dependencies)

## Setup

```bash
uv venv
uv sync
uv run playwright install chromium
```

## Usage

```bash
uv run spotm3u
uv run spotm3u --help
uv run spotm3u --version
```

The installed command can also be used directly after activating the uv
environment:

```bash
source .venv/bin/activate
spotm3u
```

## Tests

```bash
uv run pytest
```

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
