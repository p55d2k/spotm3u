# Tests

The suite is grouped by **what a test exercises**, not by filename. Put a new
test in the directory whose neighbours fail for the same reason.

```text
tests/
  conftest.py     shared offline fixtures, helpers, and REPO_ROOT
  unit/           one component, every external boundary mocked
    matching/     normalization, online search, ranking, source validation
    metadata/     ID3 tags, metadata caches/jobs, Apple Music catalog matching
    lyrics/       lyrics retrieval, parsing, and USLT/SYLT/sidecar writing
    artwork/      album and artist artwork resolution and embedding
    downloads/    yt-dlp downloader, download cache, audio validation, FFmpeg
    library/      local audio discovery, Exportify parsing, M3U writing
    media_player/ the platform "Add to Media Player" integration
    uploads/      Exportify ZIP uploads and upload-directory cleanup
    utilities/    models, configuration, and logging
  integration/    several components driven together
    processing/   resolution, jobs, fast mode, order/duplicates, full pipeline
    api/          the Flask app and its `/api` routes
    frontend/     locating, building, and serving the React build
    desktop/      native window shell and the production launcher
  regression/     previously found bugs and decisions that must not change
    matching/     matching regression suite, live (opt-in) checks, pinned fixes
  packaging/      release helpers: build, icons, bundles, smoke test, versioning
```

## Conventions

- **Unit tests run offline.** The root `conftest.py` already blocks artwork and
  lyrics providers for the whole suite; a test that needs a real provider
  belongs behind the `network` marker (see `regression/matching`) and must be
  skipped unless `SPOTM3U_RUN_NETWORK_TESTS=1`.
- **`slow` marks tests that shell out to real external tools.** Pre-commit runs
  `pytest -m "not slow"`; CI runs everything.
- **Import shared helpers with `from conftest import ...`** (`REPO_ROOT`,
  `export_zip`, `NoCandidates`). Pytest puts `tests/` on the path, so this works
  from any subdirectory and keeps tests independent of their nesting depth.
- **Use `tmp_path`** for files; tests never read or write outside the repository.

## Running

```bash
uv run pytest                                  # everything
uv run pytest tests/unit                       # one group
uv run pytest tests/regression/matching        # one directory
uv run pytest tests/unit/metadata/test_apple_music.py -q
uv run pytest -n auto -m "not slow"            # what pre-commit runs
```

See [development.md](../docs/development.md) for the full development setup.
