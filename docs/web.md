# Web Application

## Technology

Use Flask with:

- Jinja2
- vanilla HTML/CSS/JS unless existing project requirements require otherwise

## User Flow

Exportify
→ ZIP upload
→ playlist selection
→ processing
→ result
→ M3U download

## Processing

Each Track goes through:

1. Local audio resolution
2. Online search if no local match exists
3. Candidate ranking
4. Source validation
5. yt-dlp download
6. Downloaded-audio validation
7. Retry with another candidate if appropriate
8. Final resolution

## Processing States

Useful states include:

- queued
- resolving-local
- searching
- ranking
- validating-source
- downloading
- validating-audio
- retrying-source
- complete
- failed
- ambiguous

## Important UI Behavior

Do not show:

"Failed: no candidate passed source validation"

when the system simply has not yet attempted plausible candidates.

Differentiate:

- no plausible candidates found
- candidate rejected before download
- download failed
- downloaded audio failed validation
- multiple candidates remained ambiguous

## Progress

For each track, show the current meaningful stage.

Examples:

- Finding local audio
- Searching for source
- Checking source
- Downloading
- Checking downloaded audio
- Trying another source

## Result

Display:

- total tracks
- successful tracks
- local matches
- downloaded tracks
- failed tracks
- ambiguous tracks
- rejected tracks
- uncertain tracks

For failures, display the actual processing reason.

The finished result is available at `GET /processing/<job_id>/<playlist_id>/result`,
which renders per-track outcomes (status and reason) alongside the summary
counts. While the job is still queued or running, that route redirects to the
live processing page.

## M3U Download

When processing completes, offer a `Save playlist (M3U)` link to
`GET /processing/<job_id>/<playlist_id>/playlist.m3u`, which downloads the
generated playlist as an attachment.

The M3U and any downloaded MP3s are written to the persistent download
directory so the playlist continues to reference real local files.

## Download Directory

Downloads are stored persistently under `DOWNLOAD_DIR` config, defaulting to
`<MUSIC_LIBRARY>/spotm3u-downloads/`. This keeps files so the M3U can load
them and lets later runs match them locally instead of re-downloading.

## Important

A track should only be marked successful after the final local audio file has passed the relevant validation.

A track should not be marked failed merely because its metadata was imperfect.
