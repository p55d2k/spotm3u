# Security

## ZIP Uploads

Exportify ZIP files are untrusted input.

Protect against:

- path traversal
- absolute paths
- malicious archive contents
- oversized compressed files
- oversized extracted files
- malformed CSV/JSON

All extraction must remain inside the job's controlled directory.
Archive names are also checked against Windows-invalid characters and reserved
device names so the same upload cannot fail or escape path handling on Windows.

## Track Metadata

Spotify/Exportify metadata is untrusted input.

Never use raw:

- title
- artist
- album
- playlist name

as shell commands.

Sanitize filenames separately from shell execution.

## yt-dlp

Source URLs must be handled as data.

Do not construct shell commands through string concatenation.

Prefer direct subprocess argument arrays or yt-dlp's Python API.

Use controlled output directories.

## Resource Limits

Protect the server from:

- too many simultaneous downloads
- huge uploads
- excessive decompression
- runaway ffmpeg processes
- infinite retries
- hung downloads
- excessive temporary storage

## File Downloads

The Flask download endpoint must never accept an arbitrary filesystem path from the browser.

Resolve downloads through an internal job ID and known output filename.

## Credentials

Spotify authentication happens externally through Exportify. The application
does not collect or store Spotify passwords or authentication tokens.

Do not log authentication tokens or unrelated credentials.

## Cleanup

Temporary job data has a deterministic cleanup path:

- The uploaded ZIP archive is removed immediately after successful extraction
  into the job directory.
- Old, abandoned job directories (extracted files, job metadata, and the
  per-job output slot) are swept from the upload root on each new upload and
  at server startup once they pass the configured age limit
  (`upload.max_job_age`). Jobs still queued or running are never removed.
- Failed downloads are discarded as soon as they fail validation, so invalid
  or partial audio does not accumulate in the download directory.

The generated M3U and the successfully downloaded audio it references are kept
for as long as the application's retention policy requires — they are never
deleted before the playlist stops being useful, and captured downloads are
reused across runs via the download cache.

## Principle

Treat:

- uploaded files
- archive filenames
- track metadata
- source URLs
- downloaded media

as untrusted input.
