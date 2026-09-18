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

The playlist selection page presents each parsed playlist as an independent
button/card. Checkboxes also support Select all, Deselect all, and
**Download selected playlists**. The batch flow reuses the single-playlist
resolver, cache, download, and M3U pipeline for every selected playlist while
sharing resolved audio across the batch. Each playlist receives its own M3U
download and completion status. After a result is available, **Download another playlist**
returns to that same selection state for the upload job; the ZIP is not
uploaded or parsed again. The existing M3U download remains available from
each result.

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

## Apple Music (macOS)

On macOS, a completed result also offers **Add to Apple Music**. The app uses
the system `osascript` command to ask the Music app to create or reuse a user
playlist and add each resolved local file in playlist order. Each entry is
added separately so duplicates are preserved where Music permits them.
Resolved files retain the individual artist values in their ID3 metadata, so
collaborations are imported as collaborations rather than one concatenated
artist name.

The integration is not shown on other platforms. Music reports per-entry
errors back to the app; unresolved tracks and import failures are reported as
partial results rather than being claimed as successful. The M3U download
remains available independently.

The M3U and any downloaded MP3s are written to the persistent download
directory so the playlist continues to reference real local files.

## Download Directory

Downloads are stored persistently under `DOWNLOAD_DIR` config, defaulting to
`<MUSIC_LIBRARY>/spotm3u-downloads/`. This keeps files so the M3U can load
them and lets later runs match them locally instead of re-downloading.

## Important

A track should only be marked successful after the final local audio file has passed the relevant validation.

A track should not be marked failed merely because its metadata was imperfect.
