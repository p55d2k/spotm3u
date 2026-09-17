# spotm3u

`spotm3u` is a local Python CLI for turning Spotify playlist metadata into
portable `.m3u` playlists. It is designed around the normal Spotify website
and a local music library; it does not use the Spotify Web API as a core
dependency and does not handle Spotify passwords.

The project currently supports **Task 5: switching from the visible login browser to background/headless mode while preserving the authenticated session**.
Running the CLI opens a visible Spotify browser window where the user signs in
directly; the CLI detects the authenticated state without receiving or storing
credentials. Once the session is authenticated, the app reuses the same local
browser profile and can switch to a headless/background Chromium context for
subsequent automation without forcing the user to log in again. Playlist
extraction, local audio matching, and M3U writing will be added in later tasks.

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

`spotm3u` opens Spotify in a visible Chromium window and waits for the user to
complete login on Spotify's own page when no valid local session exists. The
authenticated Chromium profile is stored in `.browser-data/` in the current
directory. It is local-only, ignored by Git, and must not be copied or
committed.

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
