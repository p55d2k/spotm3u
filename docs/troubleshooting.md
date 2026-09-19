# Troubleshooting

## The server does not start

Check the terminal for the configuration error and confirm that port 5001 is
available. To use another port, set `web.port` in `config.toml`. Packaged
launches bind to `127.0.0.1`; the application is not intended to be exposed
directly to the network.

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

## Apple Music import is unavailable

The integration is shown only on macOS and requires the system Music app.
The M3U download remains available on every supported platform.
