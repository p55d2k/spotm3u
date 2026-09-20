# Troubleshooting

## The server does not start

Configuration errors are reported in the terminal for source runs. A packaged
Windows build has no console, so it shows a dialog instead.

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

## The macOS app runs but never shows a window

The reliable macOS install path is the `.pkg` release: the installer writes
`SpotM3U.app` to `/Applications` fresh, so it carries no quarantine attribute
and launches normally. If instead you launched a ZIP-extracted, directly
downloaded, or DMG-dragged copy, macOS marks the unsigned app as quarantined,
and on recent macOS versions the app stops inside the dynamic loader before it
can finish launching: the process shows up in Activity Monitor, never opens its
window, never starts the Flask server, and cannot be quit or removed in the
normal way. macOS 26 in particular also re-stamps quarantine onto files copied
out of a quarantined disk image, so the DMG is not exempt.

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
provider services loopback-only because they are unauthenticated.

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
