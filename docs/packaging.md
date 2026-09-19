# Packaging

Packaging screenshots are intentionally not planned: the standalone archive
is better documented by its supported-platform table and smoke test than by a
static image. UI screenshots belong to the
[screenshot plan](screenshots.md).

Standalone builds are PyInstaller one-folder bundles. The source application
and the packaged application use the same Flask app, but the packaged launcher
starts without Flask's debug reloader and discovers configuration inside the
bundle or beside the executable.

## Build locally

Build dependencies are separate from runtime and development dependencies:

```bash
uv sync --locked --group build
SPOTM3U_FFMPEG_DIR=/absolute/path/to/ffmpeg-stage \
  uv run --group build pyinstaller --noconfirm --clean packaging/spotm3u.spec
```

`SPOTM3U_FFMPEG_DIR` is optional for a local build, but a bundle made without
it needs FFmpeg from the system. When supplied, the directory must contain
`ffmpeg` and `ffprobe` (with `.exe` on Windows); the files are copied into the
bundle's `ffmpeg/` directory. The resulting application is
`dist/spotm3u/`.

The spec collects package templates/static assets, yt-dlp dynamic modules,
bgutil plugin modules, and zhconv data. `packaging/run_app.py` is the
PyInstaller entry point; do not replace it with a bare package module.

## FFmpeg and external providers

CI stages architecture-specific FFmpeg binaries before building and includes
them in every release archive. The release workflow currently uses the
platform-specific GitHub runners listed in the README. FFmpeg is a separate
process and is not linked into spotm3u, but distributed binaries retain their
own license and source obligations. Keep the exact FFmpeg license/source
notice with release artifacts.

The optional bgutil PO-token provider is not bundled. Users who configure an
HTTP provider must run it separately; script providers require their supported
Node.js or Deno runtime.

## Output and verification

PyInstaller writes intermediate files to `build/` and the bundle to `dist/`.
The release workflow archives the bundle and runs `packaging/smoke_test.py`
against each extracted archive. The smoke test checks startup, the HTTP
endpoint, templates/static assets, and bundled FFmpeg.
