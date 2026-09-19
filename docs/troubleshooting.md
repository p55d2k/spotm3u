# Troubleshooting

## The server does not start

Configuration errors are reported in the terminal for source runs. A packaged
Windows build has no console, so it shows a dialog instead.

spotm3u prefers the `web.port` from `config.toml` (default 5001) and falls back
to a free port when that port is already taken, so another program using 5001
does not stop it from starting. The port actually in use is the one the browser
opens; the startup log names it for source runs.

Packaged launches bind to `127.0.0.1` only; the application is not intended to
be exposed directly to the network.

## The browser does not open

spotm3u opens its interface in the default browser once the server is ready.
Set `SPOTM3U_NO_BROWSER=1` to disable that and open the printed URL yourself;
the startup log reports it as `listening on http://127.0.0.1:<port>`.

If no browser starts, the application is still running: open the printed URL
manually. A machine without a registered default browser cannot be opened
automatically.

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
