# Troubleshooting

## The server does not start

Configuration errors are reported in the terminal for source runs. A packaged
Windows build has no console, so it shows a dialog instead. If the packaged
launcher exits before the window appears, inspect `spotm3u-error.log` and
`spotm3u-startup.log` beside `SpotM3U.exe`; if that folder is not writable, the
same files are written to Windows' temporary directory (`%TEMP%`).

SpotM3U prefers the `web.port` from `config.toml` (default 5001) and falls back
to a free port when that port is already taken, so another program using 5001
does not stop it from starting. The port actually in use is the one opened in
the native window; the startup log names it for source runs.

Packaged launches bind to `127.0.0.1` only; the application is not intended to
be exposed directly to the network.

## The native window does not open

The packaged application opens its interface in a native `SpotM3U` window, not
a browser. Set `SPOTM3U_NO_WEBVIEW=1` to disable that window for headless or
scripted runs; the startup log still reports it as
`listening on http://127.0.0.1:<port>` so the server can be reached manually.

If no window appears, the application may still be running: open the printed
URL in a browser or check the startup log. A machine without a windowing system
cannot display a native window.

### Windows: double-clicking `SpotM3U.exe` appears to do nothing

The Windows build is windowed (no console), so a failure to open the window can
look like the application never started. Two causes are handled automatically by
the packaged build:

- **Mark of the Web.** A ZIP downloaded in a browser and extracted with File
  Explorer marks every extracted file as coming from the internet. pywebview
  reaches its native backend through pythonnet, and the .NET Framework refuses
  to load an assembly carrying that mark unless the host allows it. The release
  ships `SpotM3U.exe.config` beside `SpotM3U.exe` with `loadFromRemoteSources`
  enabled, which lets the bundled assemblies load. If that file is missing (for
  example because the bundle was built by hand), or your extraction tool dropped
  it, unblock the folder once:

  ```powershell
  Get-ChildItem -Recurse | Unblock-File
  ```

- **WebView2 Runtime.** pywebview renders through the Microsoft Edge WebView2
  Runtime. If it is missing, install the Evergreen WebView2 Runtime, then launch
  `SpotM3U.exe` again.

Every packaged launch writes `spotm3u-startup.log` beside `SpotM3U.exe` (or in
`%TEMP%` when that folder is not writable), and a failed launch additionally
leaves `spotm3u-error.log` with the fatal message and traceback. The startup log
captures the full startup sequence, so a launch that dies before any dialog
appears still records the real reason on disk. To run without the native window
while diagnosing a bundle, set `SPOTM3U_NO_WEBVIEW=1` in PowerShell; the server
then listens at `http://127.0.0.1:5001/` (or the port shown in the log).

## The macOS app runs but never shows a window

The reliable macOS install path is the `.pkg` release: the installer writes
`SpotM3U.app` to `/Applications` fresh, so it carries no quarantine attribute
and launches normally. If instead you launched a ZIP-extracted or directly
downloaded copy, macOS marks the unsigned app as quarantined, and on recent
macOS versions the app stops inside the dynamic loader before it can finish
launching: the process shows up in Activity Monitor, never opens its window,
never starts the Flask server, and cannot be quit or removed in the normal way.
macOS 26 in particular also re-stamps quarantine onto files copied out of a
quarantined disk image, so no unsigned copy can dodge it.

To tell this apart from a still-starting app: the log line
`SpotM3U listening on http://127.0.0.1:<port>` is never printed, and nothing
listens on the port.

Fix:

1. Quit the stuck process: Activity Monitor → select SpotM3U → **Quit**, or
   run `killall SpotM3U` in Terminal. The app must stop before you can delete
   it — the hung process keeps the bundle locked.
2. Clear the quarantine attribute once:

   ```bash
   xattr -cr /path/to/SpotM3U.app
   ```

3. Launch `SpotM3U.app` again; it opens its window normally.

Installing the `.pkg` never needs these steps: the installer's files carry no
quarantine attribute in the first place. The project does not disable
Gatekeeper; the package simply avoids marking the unsigned app as quarantined.

## Online downloads fail because FFmpeg is missing

Source runs use yt-dlp's FFmpeg lookup. Install FFmpeg and make sure both
`ffmpeg` and `ffprobe` are available on `PATH`, or use a standalone release
that includes them. A packaged bundle can also use an `ffmpeg/` directory
beside the executable as an override.

## YouTube downloads

The bgutil Python plugin is installed with the application, but installing it
does not start a provider. An HTTP provider must be running on the configured
URL, or a script provider must point to a valid checkout and runtime. Keep
provider services loopback-only because they are unauthenticated. Raise
`download.pot_provider_timeout` (seconds, default `5`) when a provider that is
still starting up is reported as unreachable.

PO tokens are not browser authentication. If YouTube requires a signed-in
session, set `download.cookies_from_browser` (for example, `chrome`) or
`SPOTM3U_YTDLP_BROWSER=chrome`. Sign in to YouTube in that browser and close it
if yt-dlp cannot read its cookie database. Cookies are read locally and should
never be committed or pasted into an issue.

Lower `download.workers` when requests are rate-limited. Private,
members-only, age-restricted, or CAPTCHA-protected videos may remain
unavailable even with a configured browser.

## Tracks are unresolved

Review the result page's reason for each track. Matching is intentionally
conservative about wrong artists, covers, and non-music audio, while incomplete
metadata can remain uncertain. Retry unresolved tracks after correcting the
local library or online configuration.

## Add to Media Player is unavailable

**Add to Media Player** is shown only on macOS (Apple Music) and Windows (the
generated M3U is opened with its default associated player). The button is
also hidden when no track resolved, because there would be nothing to import.
On Windows, a missing M3U file association fails with a message suggesting the
manual download; install or associate a player that supports M3U and retry.
The M3U download remains available on every supported platform.
