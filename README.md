# SpotM3U

[![CI](https://github.com/p55d2k/spotm3u/actions/workflows/ci.yml/badge.svg)](https://github.com/p55d2k/spotm3u/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

SpotM3U turns Spotify playlists exported from [Exportify](https://exportify.net/) into M3U playlists for music already on your computer.

## Download

- [Download for Windows](https://github.com/p55d2k/spotm3u/releases)
- [Download for macOS](https://github.com/p55d2k/spotm3u/releases)
- [Download for Linux](https://github.com/p55d2k/spotm3u/releases)

## Quick start

Exportify → Download → Launch → Upload ZIP → Get M3U

1. Exportify → export your Spotify playlists and download the ZIP.
2. Download → get the SpotM3U app for your platform.
3. Launch → start the app; SpotM3U opens its interface in a native window.
4. Upload ZIP → drag in the Exportify ZIP and choose playlists.
5. Get M3U → review matches and download the generated playlist.

## Overview

SpotM3U is a local Flask application that turns playlists exported from
[Exportify](https://exportify.net/) into M3U playlists for audio already on
your computer. When a track is not found locally, it can search for and
download a candidate recording, validate it, and keep the resulting MP3 for
later use.

The application does not log in to Spotify, request Spotify credentials, use
the Spotify Web API, scrape Spotify, or require Spotify Premium. Spotify
authentication and export happen in Exportify; SpotM3U receives the downloaded
ZIP only.

## How it works

1. **Export** your playlists with Exportify and download its ZIP.
2. **Upload** the ZIP to SpotM3U. The playlists are parsed locally.
3. **Choose** one or more playlists to process.
4. **Resolve** tracks from your local library first. Unmatched tracks can be
   searched, validated, and downloaded.
5. **Review** matched, unresolved, rejected, failed, and ambiguous tracks.
6. **Download** the generated M3U playlist.

Playlist order and intentional duplicate entries are preserved. Missing,
rejected, failed, and ambiguous tracks are reported instead of silently being
written as successful entries. On macOS, a completed playlist can also be sent
to the Music app.

![SpotM3U result page](docs/images/result-page.png)

## Installation

### Standalone release

Download the archive for your platform from
[GitHub Releases](https://github.com/p55d2k/spotm3u/releases), extract it, and
start the application. SpotM3U starts a local server on a free loopback port
and opens its interface in a native `SpotM3U` window, so there is no URL to
type and no terminal to keep open.

The release archives built by CI include a Python runtime, application
dependencies, and FFmpeg/ffprobe. They do not require Python, `uv`, or a
system FFmpeg installation. CI currently builds:

| Platform | Architecture | Archive suffix | Launch |
| --- | --- | --- | --- |
| Windows | x86_64 | `windows-x86_64` | Double-click `SpotM3U.exe` |
| macOS | arm64 | `macos-arm64` | Open `SpotM3U.app` (see below) |
| Linux | x86_64 | `linux-x86_64` | Run `SpotM3U` |

The server binds to `127.0.0.1` only, so the interface is not reachable from
other machines. It prefers the port configured in `config.toml` and otherwise
picks a free one; the window always shows the UI bound to the port actually in
use.

None of the releases are signed or notarized. The project does not use Apple
Developer Program membership, Developer ID certificates, or Apple's
notarization service, so macOS and Windows may show a security prompt on first
launch. **Add to Media Player** is offered on macOS, where it adds to Apple
Music, and on Windows, where the generated M3U opens with its default
associated media player. A YouTube PO-token provider is an optional external
service and is not bundled; see
[YouTube downloads](docs/troubleshooting.md#youtube-downloads).

### Windows first launch

The Windows build has no console window: double-clicking the executable starts
SpotM3U silently and opens its native window.

```text
Download SpotM3U-<version>-windows-x86_64.zip
        ↓
Extract it and double-click SpotM3U.exe
        ↓
SpotM3U opens in its native window
```

If SpotM3U cannot start, it shows a dialog explaining the problem, since a
windowed application has no console to print a traceback to.

### macOS first launch

The macOS archive contains a normal application bundle, `SpotM3U.app`. Because
the build is not Apple-signed or notarized, macOS may refuse to open it on the
first double-click. Approve it once through Apple's standard flow:

1. Download `SpotM3U-<version>-macos-arm64.zip` from
   [GitHub Releases](https://github.com/p55d2k/spotm3u/releases).
2. Extract the ZIP. You get `SpotM3U.app`.
3. Right-click (Control-click) `SpotM3U.app` and choose **Open**.
4. If macOS shows a security warning, choose **Open** to confirm.
5. SpotM3U starts and opens its interface in its native window.

This approval is needed only once. Afterwards, launch `SpotM3U.app` with a
normal double-click like any other app.

The exact wording and appearance of the first-launch prompt varies between
macOS versions, so the steps above describe the flow rather than one specific
dialog. This is Apple's normal way to approve an unsigned app: do not disable
Gatekeeper and do not run `xattr` to strip the quarantine attribute. Those
steps are not part of the installation, and the release does not attempt to
bypass Gatekeeper.

### Run from source

Source usage requires Python 3.13+, [`uv`](https://docs.astral.sh/uv/), and
FFmpeg on `PATH` when online downloads need audio conversion:

```bash
git clone https://github.com/p55d2k/spotm3u.git
cd spotm3u
uv sync
uv run dev
```

`uv run dev` starts the Flask development server on
<http://127.0.0.1:5001/> (binding to loopback only) with the debug reloader, so
frontend and backend changes are picked up without rebuilding. The UI is used
in a normal browser with full developer tools.

Use `uv run app` to try the production desktop workflow from source: it starts
the same Flask app on a free loopback port and shows it inside a native
`SpotM3U` window instead of a browser. `uv run build` produces the
distributable application; see
[Development](docs/development.md) and [Packaging](docs/packaging.md).
No Spotify account credentials or API key are needed by SpotM3U.

## Usage

1. Export playlists with Exportify and download its ZIP.
2. Start SpotM3U and upload the ZIP.
3. Select one or more playlists.
4. Review local matches and any online resolution results.
5. Download the generated M3U.

![SpotM3U playlist selection](docs/images/playlist-selection.png)

The M3U references existing files and successfully downloaded MP3s. The
download directory defaults to `~/Music/SpotM3U-downloads/` and can be changed
in `config.toml`. Configuration is optional; see the commented
[config.toml](config.toml) example.

YouTube downloads can require a signed-in browser session. Configure
`download.cookies_from_browser` only when needed; browser cookies are read
locally by yt-dlp and are not stored or logged by SpotM3U. PO tokens do not
replace browser authentication. Provider setup and common failures are
documented in [troubleshooting](docs/troubleshooting.md).

![SpotM3U processing progress](docs/images/processing-progress.png)

## Documentation

- [Development](docs/development.md) — local setup, checks, and project layout
- [Architecture](docs/architecture.md) — application boundaries and data flow
- [Packaging](docs/packaging.md) — PyInstaller bundles and FFmpeg staging
- [Releases](docs/releases.md) — tags, CI builds, verification, and publishing
- [Troubleshooting](docs/troubleshooting.md) — configuration and download issues
- [Exportify format](docs/exportify.md) — parser boundary and playlist semantics
- [Matching and source validation](docs/matching.md)
- [M3U output](docs/m3u.md)
- [Security design](docs/security.md)
- [Screenshot plan](docs/screenshots.md)

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development and pull request
workflow. Report vulnerabilities privately according to
[SECURITY.md](SECURITY.md); do not open a public issue for an undisclosed
security problem.

## License

SpotM3U is released under the [MIT License](LICENSE). The project uses
third-party software including Flask, Mutagen, yt-dlp, bgutil-ytdlp-pot-provider,
and FFmpeg. Release bundles must retain the applicable notices and source
information for distributed FFmpeg builds.
