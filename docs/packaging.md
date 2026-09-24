# Packaging

The packaged application is documented by its supported-platform table and
smoke test rather than by screenshots.

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
non-zero. macOS and Linux keep their console for logs. The Windows bundle also
ships `SpotM3U.exe.config` beside the executable so .NET Framework will load
the bundled pythonnet assembly even when a browser-downloaded ZIP marked it with
the Mark of the Web.

## Application icon

`assets/icon.png` is the single source of truth for the SpotM3U icon and is
tracked by Git. `uv run build` (via `src/spotm3u/build.py`) regenerates the
platform formats the packaging tools need from it before PyInstaller runs:

- `assets/generated/icon.ico` – embedded in the Windows executable (taskbar,
  File Explorer, and shortcuts).
- `assets/generated/icon.icns` – embedded in the macOS `SpotM3U.app` bundle
  (Finder, Dock, Applications folder).

Generation is handled by `packaging/generate_icons.py`. It first validates the
master artwork (present, valid 8-bit RGB/RGBA PNG, square, at least
512x512) and fails the build with a clear reason otherwise, and it removes
previously generated icons before writing new ones so a stale artifact can never
be packaged. The generated directory `assets/generated/` is git-ignored;
`assets/icon.png` remains tracked. The canonical PNG is also collected into the
bundle so the desktop window can apply it at runtime on Linux (the only pywebview
backend that supports a window icon).

The Windows `.ico` holds 16, 24, 32, 48, 64, 128, and 256 pixel members and is
written with the standard library alone. On macOS the `.icns` is built by the
system's `iconutil` from a standard `.iconset`, which is what gives the icon
its 1x and @2x members for 16/32/128/256/512 points; the build fails instead of
shipping a partial icon if `iconutil` errors. Where `iconutil` is unavailable
(the Linux and Windows runners, which never embed an `.icns`) a standard-library
writer emits the same element set, so those builds stay tool-free and the
containers remain inspectable off macOS. Local and GitHub Actions builds produce
identical assets either way.

Every platform build verifies the packaged result before it is archived, so a
release cannot ship an application whose icon is missing or wrong:

- Windows — `packaging/verify_packaged_icons.py` parses the resource directory
  of `dist/SpotM3U/SpotM3U.exe` and compares its embedded icon resources member
  for member against `assets/generated/icon.ico`.
- Linux — the same script checks that the bundle carries the canonical
  `icon.png` where the frozen app looks for it (`_internal/` first, then the
  bundle root, the same candidates `spotm3u.runtime.bundle_roots` reports) and
  that it is byte-identical to the tracked master artwork. That PNG is the
  window icon the GTK backend applies; Linux gets no generated icon variants.

The packaged macOS app is an ordinary foreground application: the spec overrides
PyInstaller's `LSBackgroundOnly` default (PyInstaller sets it for console-mode
executables) and sets `NSHighResolutionCapable`, so the Dock, Finder, and the
Cmd-Tab switcher present the bundled `.icns` normally rather than falling back to
the generic placeholder. The release workflow therefore verifies the built
`dist/SpotM3U.app` with `packaging/verify_macos_bundle.py` before the bundle is
zipped, packaged, and uploaded, and the same check runs again on the published
artifacts. It fails a bundle whose `Info.plist` drops `CFBundleIconFile`, names
an icon that is not in the bundle, disables the `LSBackgroundOnly` override,
omits `NSHighResolutionCapable` (which would make macOS scale the icon), or
ships a malformed or truncated `.icns`.

## Build locally

Run the project shortcut; the build command in `pyproject.toml`
(`spotm3u.build`) wraps PyInstaller, so developers never need to invoke the
packaging tool directly:

```bash
uv run build
```

`uv run build` builds the React frontend first (`npm ci` against the committed
lockfile, then `npm run build`), regenerates the platform icons, and only then
runs PyInstaller, so the distributable always carries the UI of the checkout it
was built from. Node.js with npm is therefore a build-time prerequisite (see the
[development prerequisites](development.md#prerequisites)); the packaged
application itself needs neither Node nor a Python install. Set
`SPOTM3U_SKIP_FRONTEND=1` to package the existing `frontend/dist` without
rebuilding it - useful while iterating on `packaging/spotm3u.spec`, never part of
a release.

`uv run build` installs the locked `build` dependency group on first use (a
global PyInstaller install is never required) and produces a fresh application
for the current platform only; cross-compilation is not supported. Build
intermediates go to `build/` and the distributable to `dist/`, both git-ignored.
The command prints where the artifact was written, for example `dist/SpotM3U/`
plus `dist/SpotM3U.app/` on macOS. On macOS it then wraps the same `.app` into a
release-identical installer package, naming it
`dist/SpotM3U-<version>-macos-<arch>.pkg` (version from
`SPOTM3U_APP_VERSION`, defaulting to `0.0.0`). The packaging script can also be
run on its own:

```bash
uv run -q python packaging/make_pkg.py \
  --version 0.1.0 \
  dist/SpotM3U.app \
  dist/SpotM3U-0.1.0-macos-arm64.pkg
```

A local build reports the `__version__` committed in `src/spotm3u/__init__.py`,
which is what the in-app update check reads. To build a bundle that reports a
specific release version, stamp it first:

```bash
uv run -q python packaging/stamp_version.py 1.2.3
```

The release workflow does this automatically from the pushed tag; commit a real
version before stamping so the tree is not left reporting a release that does
not match it.

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

`assets/icon.png` is deliberately a square, full-bleed master: the macOS
platform presentation is provided by the bundle metadata above, not by baked-in
rounded corners in the artwork.

## The React frontend in the bundle

`frontend/dist` is collected into the bundle as `frontend/`, which is where
`spotm3u.frontend` looks for it when it runs frozen (after
`SPOTM3U_FRONTEND_DIST` and before a source checkout). The spec fails the build
when the directory is missing, so a release cannot silently ship an application
whose interface answers 404.

Flask serves that build at `/`, and the smoke test fetches the entry point plus
every script it references, which is what catches assets that were not
collected. The macOS bundle check requires `frontend/index.html` in the bundle
as well.

## Collected modules

The spec collects yt-dlp dynamic modules,
bgutil plugin modules, zhconv data, the syncedlyrics lyrics providers (pulled in
through `spotm3u.lyrics`), and the pywebview desktop shell and its platform
backends. `packaging/run_app.py` is the
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

On macOS the release workflow additionally publishes
`SpotM3U-<version>-macos-<arch>.pkg`, built with `packaging/make_pkg.py`.
`pkgbuild` packages the `.app` so it installs to `/Applications`; a `.pkg` is
the reliable artifact for an unsigned app because the installer writes the
payload files fresh, so the installed app carries no `com.apple.quarantine`
attribute and launches normally on modern macOS. A browser-downloaded ZIP sets
that attribute, and a quarantined, unsigned PyInstaller app hangs in the loader
on modern macOS (the process runs with no window and no server); macOS 26 also
re-stamps quarantine onto files copied out of a quarantined disk image, so
there is no unsigned archive format that dodges it. The macOS ZIP is still
built and verified in CI so the smoke test exercises the exact bundle layout
users would get, but it is not attached to the published release.

The release workflow then verifies each archive:

- `packaging/verify_macos_bundle.py` checks the macOS ZIP for a valid
  `SpotM3U.app` (`Contents/MacOS/SpotM3U`, `Info.plist`, bundled FFmpeg,
  application resources, and the built React application). The macOS PKG is expanded with
  `pkgutil --expand-full` and passed through the same
  check before upload. Signature and Gatekeeper status are intentionally
  not checked.
- `packaging/smoke_test.py` launches the packaged executable, serves the home
  page (React entry point), loads the script bundles it references, exercises
  the `/api` surface, and confirms the bundled FFmpeg. It accepts both
  the one-folder layout and the macOS `.app` bundle. Readiness is taken
  from the HTTP response on the configured port rather than from the startup
  log, so the windowed Windows build is verified the same way as the rest, and
  `SPOTM3U_NO_WEBVIEW=1` keeps the native window from opening during the check.
