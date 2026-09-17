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

The application should not collect or store the user's Spotify password.

Spotify authentication happens externally through Exportify.

Do not log authentication tokens or unrelated credentials.

## Cleanup

Temporary ZIPs, extracted files, and failed job data should eventually be removed.

Successful downloaded audio should remain available for as long as the application's retention policy requires.

## Principle

Treat:

- uploaded files
- archive filenames
- track metadata
- source URLs
- downloaded media

as untrusted input.
