# Release Architecture

This document defines how the Flask app is intended to be distributed as a
standalone downloadable application. No release workflow is implemented yet;
this records the packaging plan, the runtime file inventory, and the open
blockers.

## Intended Distribution Format

- A **standalone executable** per OS/architecture (e.g. PyInstaller one-folder
  build with a launcher executable), with the Python runtime and all Python
  dependencies bundled inside the executable directory.
- **FFmpeg/ffprobe distributed alongside the executable** so yt-dlp can find
  them without a system install.
- A **ZIP archive** as the release artifact, containing the executable
  directory, FFmpeg binaries, and a short `README.txt`.
- macOS Apple Music import is not packaged: it shells out to the system
  `osascript` and the user's Apple Music app, so it only exists on macOS and
  cannot be bundled.

## Production/Packaging Entry Point

- The packaging entry point is the console script defined in `pyproject.toml`:
  `spotm3u = "spotm3u.app:run"`.
- `spotm3u.app.run()` (`src/spotm3u/app.py:710`) currently starts the Flask
  development server (`app.run(port=..., debug=True)`). This is a development
  assumption.
- The packaged launcher must call a **non-debug production entry point**; it
  cannot reuse `run()` as-is. This is a blocker (see below).

## Runtime Files

The Flask app owns nothing outside the `spotm3u` package, so the wheel build
(`hatch build`, `packages = ["src/spotm3u"]`) already collects everything:

- Application modules: `app.py`, `apple_music.py`, `config.py`, `jobs.py`,
  `log.py`, `metadata.py`, `models.py`, `normalization.py`, `resolution.py`,
  `uploads.py`.
- Subpackages: `audio/`, `exportify/`, `m3u/`, `online/`.
- Templates: `templates/*.html` (all nine templates live inside the package).
- Static assets: `static/style.css` (inside the package).
- No external CDN assets are loaded; status icons are inline SVG, so the UI
  has no extra web dependencies.

## Python Dependencies

From `pyproject.toml`, all required at runtime:

- Python >= 3.13
- `flask>=3.1`
- `mutagen>=1.47` (metadata, downloaded-audio validation)
- `requests>=2.32`
- `yt-dlp>=2026.8.19` (search + download; YouTube behavior is pinned to this
  release line)
- `zhconv>=1.4`
- `bgutil-ytdlp-pot-provider>=2.0` (yt-dlp PO Token provider plugin; its two
  providers, `youtubepot-bgutilhttp` / `youtubepot-bgutilscript`, are
  validated by spotm3u)

`yt-dlp` should be refreshed between releases because YouTube changes break old
builds; the pinned minimum must be kept current in the bundled dependency set.

## FFmpeg Dependency

- spotm3u never locates FFmpeg itself and never sets yt-dlp's
  `ffmpeg_location` option.
- `yt-dlp`'s `FFmpegExtractAudio` postprocessor (`src/spotm3u/online/
  downloader.py:488`) resolves `ffmpeg`/`ffprobe` through its own lookup: the
  process `PATH`, then standard install locations.
- Packaging implication: FFmpeg/ffprobe must either be on the system `PATH` or
  made discoverable by the launcher (prepend the bundled `ffmpeg/` directory
  to `PATH`, or pass `ffmpeg_location` through `download_track`).
- No other code in spotm3u shells out to FFmpeg; audio validation uses
  `mutagen`, not `ffprobe`.

## Other External Runtime Dependencies

- **bgutil PO token provider**: when `youtubepot-bgutilscript` is configured,
  the provider script needs a matching runtime on `PATH` (`node` or `deno`,
  `src/spotm3u/online/downloader.py:107`). The HTTP provider variant
  (`youtubepot-bgutilhttp`) needs the provider server running. Neither is
  bundled; both are user-configured options.
- **macOS Apple Music**: requires macOS, `osascript`, and the Music app
  (`src/spotm3u/apple_music.py`).

## Intended Release Artifact Structure

```text
spotm3u-<version>-<os>-<arch>.zip
└── spotm3u/                    # unpacked root
    ├── spotm3u                # launcher executable (platform-specific)
    ├── _internal/             # bundled Python runtime + pip install of
    │                          #   spotm3u, flask, yt-dlp, mutagen, ... (and
    │                          #   templates/ + static/ package data)
    ├── ffmpeg/                # ffmpeg + ffprobe native binaries
    └── README.txt             # first-run instructions (config.toml, macOS,
                               #   PO token provider, Apple Music)
```

## Packaging Assumptions and Blockers

- **Blocker — dev entry point**: `spotm3u.app.run()` runs the Flask dev server
  with `debug=True`. A production entry point is required before the standalone
  executable is usable, or the packaged app must pass `debug=False` through a
  separate entry function.
- **Config discovery**: `config.toml` is discovered from the current working
  directory (or `SPOTM3U_CONFIG`); the bundled launcher should either document
  this or set a stable config location in the jar/executable directory.
- **Writable paths assumed**: uploads default to the temp directory and
  downloads to `~/Music/spotm3u-downloads`; these must be writable and are not
  bundled.
- **Per-platform builds**: Python 3.13 provides no cross-compiling, so each
  ZIP targets exactly one OS/architecture.
- **yt-dlp freshness**: bundled yt-dlp goes stale; a release process must track
  upstream releases or rely on the fewest supported capabilities.
- **Apple Music is macOS-only** and cannot be tested or shipped on other
  platforms; the feature must degrade gracefully elsewhere (it already checks
  `sys.platform == "darwin"`).