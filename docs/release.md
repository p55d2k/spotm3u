# Release Architecture

This document defines how the Flask app is distributed as a standalone
downloadable application: the packaging plan, the build process, the runtime
file inventory, and the remaining blockers.

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

- The packaging entry is `packaging/run_app.py` (a one-line absolute import of
  `spotm3u.launcher:main`); PyInstaller treats the target script as a top-level
  module, so a bare `launcher.py` would break the package's relative imports.
  The console script `spotm3u = "spotm3u.launcher:main"` remains the source
  entry point for normal usage.
- The launcher starts the same Flask app as development (`create_app`), binds
  loopback (`127.0.0.1`), and runs without the debug reloader
  (`debug=False, use_reloader=False, threaded=True`).
- When packaged, it points yt-dlp at the bundled `ffmpeg/` directory and falls
  back to a `config.toml` in the bundle (preferring one next to the executable
  over `_internal`/`_MEIPASS`), unless `SPOTM3U_CONFIG` is set.
- The development entry (`spotm3u.app.run`) is retained as-is for the dev
  server with `debug=True`; it is not the packaging entry.

## Runtime Files

The Flask app owns nothing outside the `spotm3u` package, so the wheel build
(`hatch build`, `packages = ["src/spotm3u"]`) already collects everything:

- Application modules: `app.py`, `apple_music.py`, `config.py`, `ffmpeg.py`,
  `jobs.py`, `launcher.py`, `log.py`, `metadata.py`, `models.py`,
  `normalization.py`, `resolution.py`, `runtime.py`, `uploads.py`.
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

- FFmpeg resolution is centralized in `src/spotm3u/ffmpeg.py`.
- It never invokes FFmpeg directly; `yt-dlp`'s `FFmpegExtractAudio`
  postprocessor (`src/spotm3u/online/downloader.py`) runs it.
- `locate_ffmpeg_location()` returns the `ffmpeg_location` value handed to
  yt-dlp:
  - **Packaged release**: the bundled `ffmpeg/` directory resolved by
    `runtime.bundle_roots()` (first hit among `_MEIPASS`/`<exe>/_internal`/
    `<exe>`). This is passed to yt-dlp explicitly, so neither FFmpeg nor
    `PATH` configuration is required from the user.
  - **Development**: `None`, preserving current behavior — yt-dlp resolves
    FFmpeg through its own lookup (`PATH`, then standard install locations).
- `require_ffmpeg_location()` raises a single actionable
  `FFmpegMissingError` (install on `PATH`, or provide the bundled directory)
  when nothing is usable.
- No other code in spotm3u shells out to FFmpeg; audio validation uses
  `mutagen`, not `ffprobe`.

### Expected bundled FFmpeg file location

```text
spotm3u-<version>-<os>-<arch>.zip
└── spotm3u/                    # PyInstaller one-folder build root
    ├── spotm3u                # launcher executable
    └── _internal/
        └── ffmpeg/            # collected by the spec: FFmpeg binaries live in
            ├── ffmpeg[.exe]   #   _internal/ffmpeg/, which the frozen resolver
            └── ffprobe[.exe]  #   finds via bundle_roots()
```

`ffmpeg.py` treats the directory as usable when it contains `ffmpeg`,
`ffmpeg.exe`, or an adjacent `ffprobe`/`ffprobe.exe`. The same directory is
passed to yt-dlp as `ffmpeg_location`, which locates both binaries there. A
user placing an `ffmpeg/` directory next to the executable is also honored as
a manual override/patch. `bundled_directory()` reports the primary expected
location in the `FFmpegMissingError` message.

### FFmpeg build licensing

FFmpeg is distributed as a separate ZIP entry, never embedded. We plan to ship
a GPL build (e.g. gyan.dev builds on Windows; a GPL static build for macOS and
Linux):

- **GPL compliance**: distributing GPL-licensed FFmpeg binaries requires the
  distribution to comply with the GPL (provide corresponding source or a
  valid source offer, license text, and attribution).
- **GPL license contamination**: spotm3u is MIT-licensed. Linking/using FFmpeg
  as a separate process for audio transcoding does **not** make spotm3u a
  derivative work; the license requirements attach to the shipped FFmpeg
  binaries, not to spotm3u's own source.
- **Attribution**: the chosen static build must keep its own license/version
  files; the release `README.txt` must include FFmpeg's license notice and the
  source URL of the exact build shipped.
- The FFmpeg build must be the same architecture as the executable
  (e.g. x86_64 vs arm64) and must not require a system install.

## Other External Runtime Dependencies

- **bgutil PO token provider**: when `youtubepot-bgutilscript` is configured,
  the provider script needs a matching runtime on `PATH` (`node` or `deno`,
  `src/spotm3u/online/downloader.py:107`). The HTTP provider variant
  (`youtubepot-bgutilhttp`) needs the provider server running. Neither is
  bundled; both are user-configured options.
- **macOS Apple Music**: requires macOS, `osascript`, and the Music app
  (`src/spotm3u/apple_music.py`).

## Building a Release

PyInstaller is not a runtime dependency; it lives in the `build` dependency
group (`pyproject.toml`). A one-folder build is produced with:

```sh
uv sync --group build
# Optional: bundle FFmpeg into _internal/ffmpeg. The spec copies the CONTENTS
# of $SPOTM3U_FFMPEG_DIR there, so it must hold ffmpeg + ffprobe directly.
# Stage real (un-symlinked) binaries from e.g. a Homebrew install:
#   packaging/stage_ffmpeg.py ffmpeg-stage
SPOTM3U_FFMPEG_DIR=/abs/path/ffmpeg-stage uv run --group build pyinstaller \
  --noconfirm --clean packaging/spotm3u.spec
```

- The spec (`packaging/spotm3u.spec`) is a **one-folder** build (`EXE` with
  `exclude_binaries=True` + `COLLECT`): the executable plus `_internal/` with
  the Python runtime, dependencies, `yt_dlp_plugins` (bgutil PO token
  providers, `include_py_files=True`), `zhconv` dict data, and the app's
  templates/static.
- When `SPOTM3U_FFMPEG_DIR` is set, the spec appends that directory's contents
  as `(str(ffmpeg_dir), "ffmpeg")` so it lands in `_internal/ffmpeg/`.
- Output: `dist/spotm3u/` — run `./spotm3u` directly, no Python/venv needed.
  A `README.txt` should accompany it in the final ZIP.
- Build-machine note: signing/dwld tooling on macOS requires the Xcode license,
  so build with a real `lipo` on `PATH` (under
  `XcodeDefault.xctoolchain/usr/bin/`) or agree to the license first.

## Intended Release Artifact Structure

```text
spotm3u-<version>-<os>-<arch>.zip
└── spotm3u/                    # unpacked root = dist/spotm3u
    ├── spotm3u                # launcher executable (platform-specific)
    ├── config.toml            # optional; user may drop one here to override
    │                          #   defaults (SPOTM3U_CONFIG wins over this)
    ├── _internal/             # bundled Python runtime + pip install of
    │   │                      #   spotm3u, flask, yt-dlp, mutagen, zhconv,
    │   │                      #   bgutil plugins ... (templates/ + static/)
    │   └── ffmpeg/            # ffmpeg + ffprobe native binaries
    └── README.txt             # first-run instructions (config.toml, macOS,
                               #   PO token provider, Apple Music)
```

## Packaging Assumptions and Blockers

- **Config discovery**: `config.toml` is discovered from the current working
  directory (or `SPOTM3U_CONFIG`); the bundled launcher falls back to a
  `config.toml` in the bundle when packaged — preferring one next to the
  executable over the collected-data locations — honoring an explicit
  `SPOTM3U_CONFIG` first.
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