# Security

SpotM3U runs locally and treats a handful of categories as untrusted input:
uploaded files, archive filenames, track metadata, source URLs, and downloaded
media. The application never executes uploaded files, never constructs shell
commands from metadata or URLs, and never collects or stores Spotify
credentials. The development server binds to `127.0.0.1` only and is not meant
to be exposed to a network.

## ZIP uploads

Exportify ZIP files are untrusted. Extraction is confined to the job's
controlled directory, and the parser guards against path traversal, absolute
paths, malicious archive contents, oversized compressed and extracted files,
and malformed CSV/JSON. Archive names are also checked against Windows-invalid
characters and reserved device names, so the same upload cannot fail or escape
path handling on Windows.

## Track metadata

Spotify/Exportify metadata (title, artist, album, playlist name) is untrusted
and is never used as a shell command. Filename sanitization and any external
process invocation are separate concerns.

## yt-dlp and command execution

Source URLs are handled as data. External tools are invoked through direct
subprocess argument arrays or `yt-dlp`'s Python API — never through shell
string concatenation — and all `yt-dlp`/`ffmpeg` output stays in controlled job
directories.

## Resource limits

Downloads and processing are bounded so the app cannot be overwhelmed by its
own workload: simultaneous downloads are capped, uploads and decompression are
size-limited, `ffmpeg` processes are contained, retries are capped, hung
downloads time out, and temporary storage has a cleanup path.

## File downloads

The download endpoint never accepts an arbitrary filesystem path from the
browser. Downloads resolve through an internal job ID and a known output
filename.

## Add to Media Player

The **Add to Media Player** action only ever hands over the playlist SpotM3U
itself generated for the current job; it never regenerates the playlist and it
never accepts a browser-supplied path. On macOS the resolved files are passed
to `osascript` as data inside a fixed AppleScript — every value quoted, never
rendered as a shell command. On Windows the playlist is opened through its
default file association (`os.startfile`) with a single path argument, so no
shell parses it and no user input reaches a command line.

## Credentials

Spotify authentication happens externally through Exportify. SpotM3U does not
collect or store Spotify passwords or authentication tokens, and it never logs
authentication tokens or unrelated credentials. Browser cookies used by
`yt-dlp` for signed-in YouTube access are read locally and never stored or
logged.

## Cleanup and retention

Temporary job data has a deterministic lifecycle:

- the uploaded ZIP is removed immediately after successful extraction
- abandoned job directories are swept from the upload root on each new upload
  and at server startup once they pass the configured age limit
  (`upload.max_job_age`); jobs that are still queued or running are never
  removed
- failed downloads are discarded as soon as they fail validation, so invalid or
  partial audio does not accumulate

The generated M3U and the successfully downloaded audio it references are kept
as long as they are useful — captured downloads are reused across runs via the
download cache (see [downloads](downloads.md)).