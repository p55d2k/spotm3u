# Packaging

Packaging screenshots are intentionally not planned: the standalone archive
is better documented by its supported-platform table and smoke test than by a
static image. UI screenshots belong to the
[screenshot plan](screenshots.md).

Standalone builds are PyInstaller one-folder bundles, wrapped in a normal
`SpotM3U.app` application bundle on macOS. The source application and the
packaged application use the same Flask app, but the packaged launcher starts
the desktop WebView shell instead of Flask's debug reloader and discovers
configuration inside the bundle or beside the executable.

The desktop shell binds to `127.0.0.1`, prefers the configured `web.port`, and
falls back to a free loopback port when it is taken. It then opens the UI in a
native `SpotM3U` window (pywebview) once the server accepts connections,
instead of an external browser. Closing the window shuts Flask down and exits
the process. On Windows the executable is built windowed (`console=False`) so a
double-click never flashes a terminal; that build has no standard streams, so a
startup failure is reported through a native message box and the process exits
non-zero. macOS and Linux keep their console for logs.

## Application icon

`assets/icon.png` is the single source of truth for the SpotM3U icon and is
tracked by Git. `uv run build` (via `src/spotm3u/build.py`) regenerates the
platform formats the packaging tools need from it before PyInstaller runs:

- `assets/generated/icon.ico` – embedded in the Windows executable (taskbar,
  File Explorer, and shortcuts).
- `assets/generated/icon.icns` – embedded in the macOS `SpotM3U.app` bundle
  (Finder, Dock, Applications folder).

Generation is handled by `packaging/generate_icons.py`, which uses only the
Python standard library, so no icon tool needs to be installed and local and
GitHub Actions builds produce identical assets. The generated directory
`assets/generated/` is git-ignored; `assets/icon.png` remains tracked. The
canonical PNG is also collected into the bundle so the desktop window can apply
it at runtime on Linux (the only pywebview backend that supports a window
icon).

## Build locally

Run the project shortcut; the build command in `pyproject.toml`
(`spotm3u.build`) wraps PyInstaller, so developers never need to invoke the
packaging tool directly:

```bash
uv run build
```

`uv run build` installs the locked `build` dependency group on first use (a
global PyInstaller install is never required) and produces a fresh application
for the current platform only; cross-compilation is not supported. Build
intermediates go to `build/` and the distributable to `dist/`, both git-ignored.
The command prints where the artifact was written, for example `dist/SpotM3U/`
plus `dist/SpotM3U.app/` on macOS. On macOS you can also package the release
disk image with `packaging/make_dmg.py`:

```bash
uv run -q python packaging/make_dmg.py \
  dist/SpotM3U.app \
  dist/SpotM3U-0.1.0-macos-arm64.dmg
```

When `ffmpeg-stage/` exists at the repository root, the shortcut supplies it as
`SPOTM3U_FFMPEG_DIR` automatically. The same command is what the GitHub Actions
release workflow runs, so a local build matches a release build for your
platform.

Build dependencies are separate from runtime and development dependencies
(`dependency-groups.build` in `pyproject.toml`). The equivalent explicit
invocation is:

```bash
uv sync --locked --group build
SPOTM3U_FFMPEG_DIR=/absolute/path/to/ffmpeg-stage \
  uv run --group build pyinstaller --noconfirm --clean packaging/spotm3u.spec
```

`SPOTM3U_FFMPEG_DIR` is optional for a local build, but a bundle made without
it needs FFmpeg from the system; the shortcut only supplies it when
`ffmpeg-stage/` exists. When supplied, the directory must contain
`ffmpeg` and `ffprobe` (with `.exe` on Windows); the files are copied into the
bundle's `ffmpeg/` directory. The resulting one-folder application is
`dist/SpotM3U/` on every platform. On macOS the same build also produces
`dist/SpotM3U.app`, a normal application bundle whose executable lives in
`Contents/MacOS/` and whose runtime files live under `Contents/Frameworks`.

The spec collects package templates/static assets, yt-dlp dynamic modules,
bgutil plugin modules, zhconv data, and the pywebview desktop shell and its
platform backends. `packaging/run_app.py` is the
PyInstaller entry point; do not replace it with a bare package module. On
macOS a `BUNDLE` target wraps the one-folder output as `SpotM3U.app`; the
PyInstaller bootloader uses the `.app/Contents/MacOS` location to find
`sys._MEIPASS` in `Contents/Frameworks`. The `BUNDLE` embeds
`assets/generated/icon.icns`, and the Windows executable embeds
`assets/generated/icon.ico`; both are derived from `assets/icon.png` before the
build (see "Application icon").

## Linux desktop dependencies

On Linux the pywebview window is served by GTK3 and WebKitGTK. The source build
(`uv run app`) needs the PyGObject binding supplied by the `pywebview[gtk]`
dependency plus the GTK3/WebKit system libraries, and the release runner
installs the same packages before building so PyInstaller can collect the
required GObject introspection data. The packaged Linux application in turn
expects a GTK3 desktop, which normal desktop Linux systems provide.

## FFmpeg and external providers

CI stages architecture-specific FFmpeg binaries before building and includes
them in every release archive. On Windows the workflow downloads the build from
gyan.dev and falls back to the GitHub-hosted BtbN build when that host is
unavailable, so an upstream outage does not block a release. The release
workflow currently uses the platform-specific GitHub runners listed in the
README. FFmpeg is a separate process and is not linked into SpotM3U, but
distributed binaries retain their own license and source obligations. Keep the
exact FFmpeg license/source notice with release artifacts.

The optional bgutil PO-token provider is not bundled. Users who configure an
HTTP provider must run it separately; script providers require their supported
Node.js or Deno runtime.

## Output and verification

PyInstaller writes intermediate files to `build/` and the bundle to `dist/`.
`packaging/make_archive.py` zips `dist/SpotM3U` on Windows and Linux and
`dist/SpotM3U.app` on macOS, preserving the symbolic links PyInstaller uses to
share files between `Contents/Frameworks` and `Contents/Resources`. Archives
are named `SpotM3U-<version>-<platform>-<arch>.zip`.

On macOS the release workflow additionally builds
`SpotM3U-<version>-macos-<arch>.dmg` with `packaging/make_dmg.py`, which wraps
the same `.app` into a compressed read-only disk image using Apple's
`hdiutil`. The DMG, not the ZIP, is the published macOS asset: a
browser-downloaded ZIP sets the macOS quarantine attribute, and a quarantined,
unsigned PyInstaller app hangs in the loader on modern macOS (the process runs
with no window and no server), whereas an app dragged out of a DMG is never
quarantined and launches normally. The macOS ZIP is still built and verified in
CI so the smoke test exercises the exact bundle layout users would get, but it
is not attached to the published release.

The release workflow then verifies each archive:

- `packaging/verify_macos_bundle.py` checks the macOS ZIP for a valid
  `SpotM3U.app` (`Contents/MacOS/SpotM3U`, `Info.plist`, bundled FFmpeg, and
  application resources). The macOS DMG is mounted and passed through the same
  check before upload. Signature and Gatekeeper status are intentionally
  not checked.
- `packaging/smoke_test.py` launches the packaged executable, renders the home
  template, serves a static asset, and confirms the bundled FFmpeg. It accepts
  both the one-folder layout and the macOS `.app` bundle. Readiness is taken
  from the HTTP response on the configured port rather than from the startup
  log, so the windowed Windows build is verified the same way as the rest, and
  `SPOTM3U_NO_WEBVIEW=1` keeps the native window from opening during the check.
