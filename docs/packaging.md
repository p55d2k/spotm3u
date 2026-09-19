# Packaging

Packaging screenshots are intentionally not planned: the standalone archive
is better documented by its supported-platform table and smoke test than by a
static image. UI screenshots belong to the
[screenshot plan](screenshots.md).

Standalone builds are PyInstaller one-folder bundles, wrapped in a normal
`spotm3u.app` application bundle on macOS. The source application and the
packaged application use the same Flask app, but the packaged launcher starts
without Flask's debug reloader and discovers configuration inside the bundle
or beside the executable.

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
bundle's `ffmpeg/` directory. The resulting one-folder application is
`dist/spotm3u/` on every platform. On macOS the same build also produces
`dist/spotm3u.app`, a normal application bundle whose executable lives in
`Contents/MacOS/` and whose runtime files live under `Contents/Frameworks`.

The spec collects package templates/static assets, yt-dlp dynamic modules,
bgutil plugin modules, and zhconv data. `packaging/run_app.py` is the
PyInstaller entry point; do not replace it with a bare package module. On
macOS a `BUNDLE` target wraps the one-folder output as `spotm3u.app`; the
PyInstaller bootloader uses the `.app/Contents/MacOS` location to find
`sys._MEIPASS` in `Contents/Frameworks`.

## FFmpeg and external providers

CI stages architecture-specific FFmpeg binaries before building and includes
them in every release archive. On Windows the workflow downloads the build from
gyan.dev and falls back to the GitHub-hosted BtbN build when that host is
unavailable, so an upstream outage does not block a release. The release
workflow currently uses the platform-specific GitHub runners listed in the
README. FFmpeg is a separate process and is not linked into spotm3u, but
distributed binaries retain their own license and source obligations. Keep the
exact FFmpeg license/source notice with release artifacts.

The optional bgutil PO-token provider is not bundled. Users who configure an
HTTP provider must run it separately; script providers require their supported
Node.js or Deno runtime.

## Output and verification

PyInstaller writes intermediate files to `build/` and the bundle to `dist/`.
`packaging/make_archive.py` zips `dist/spotm3u` on Windows and Linux and
`dist/spotm3u.app` on macOS, preserving the symbolic links PyInstaller uses to
share files between `Contents/Frameworks` and `Contents/Resources`. Archives
are named `spotm3u-<version>-<platform>-<arch>.zip`.

The release workflow then verifies each archive:

- `packaging/verify_macos_bundle.py` checks the macOS ZIP for a valid
  `spotm3u.app` (`Contents/MacOS/spotm3u`, `Info.plist`, bundled FFmpeg, and
  application resources). Signature and Gatekeeper status are intentionally
  not checked.
- `packaging/smoke_test.py` launches the packaged executable, renders the home
  template, serves a static asset, and confirms the bundled FFmpeg. It accepts
  both the one-folder layout and the macOS `.app` bundle.
