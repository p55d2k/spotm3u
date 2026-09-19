# Contributing

## Before opening a change

Fork and clone the repository, then install the locked development
dependencies:

```bash
git clone https://github.com/p55d2k/spotm3u.git
cd spotm3u
uv sync --locked --dev
```

Create a focused branch for the change. Keep application changes separate from
unrelated formatting or documentation edits.

## Checks

Run the relevant tests while developing and the full checks before opening a
pull request:

```bash
uv run pytest
uv run ruff check src tests packaging
uv run ruff format --check src tests packaging
```

Install pre-commit if you want these checks on every commit:

```bash
uv run pre-commit install
```

## Project conventions

Keep routes thin, put business logic in the appropriate package, and preserve
playlist order and intentional duplicate entries. Treat uploaded archives,
metadata, URLs, and downloaded media as untrusted. Do not collect Spotify
credentials or log secrets. Add or update tests for non-trivial behavior and
update the relevant documentation when user-visible behavior changes.

## Issues and pull requests

Useful issues include the operating system, Python or release version, exact
steps to reproduce, relevant logs with secrets removed, and a minimal sample
when it can be shared safely. Pull requests should explain the behavior
changed, include tests or a reason tests are not applicable, and call out
platform-specific effects. Do not include downloaded media, browser cookies,
local configuration, build output, or generated release archives.
