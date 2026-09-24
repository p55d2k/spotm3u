# JSON API

The React frontend talks to SpotM3U exclusively through `/api/...` routes
(`src/spotm3u/api.py`). They are a thin layer over the same helpers the Jinja
pages use (`src/spotm3u/web_jobs.py`), so no matching, download, metadata, or
lyrics logic lives in the HTTP layer, and both frontends read and write the
same job state while the migration is in progress.

The routes are served by the same Flask application as the pages, so they are
same-origin in a production build. During development Vite proxies `/api/*` to
Flask (see [development](development.md#frontend)).

## Response format

A successful call answers with the resource itself - never a wrapper. The
payloads are the ones the pages already receive from their own JSON endpoints
(`ProcessingJob.snapshot()` and the batch summary from
`spotm3u.web_jobs._batch_status`), so a job's `status`, `tracks`, `counts`,
`progress_total`, and `selected_playlist_ids` mean the same thing in both
frontends.

A failed call answers with:

```json
{ "error": "That upload has expired. Please upload the ZIP again.", "code": "job_expired" }
```

`error` is user-facing text that can be shown as-is; `code` is stable and is
what the frontend branches on. Uploads are bounded by `[upload] max_upload_size`
and answer `413 upload_too_large` as JSON instead of the page.

### Error codes

| Code | Status | Meaning |
| --- | --- | --- |
| `upload_missing_file` | 400 | No archive was sent (browser field or native dialog handoff). |
| `upload_invalid` | 400, 500 | The archive could not be stored, is not a ZIP, or is not an Exportify export. |
| `upload_too_large` | 413 | The archive exceeds the configured size limit. |
| `job_expired` | 404 | The upload's job directory is gone, or the upload belongs to another session. |
| `playlists_unreadable` | 400 | The stored export can no longer be parsed. |
| `playlist_unavailable` | 400, 404 | The playlist id is not part of this export. |
| `selection_required` | 400 | Neither the body nor the job state selects a playlist. |
| `selection_expired` | 404 | This playlist is not part of the current selection. |
| `job_not_found` | 404 | No conversion has been started for that playlist yet. |
| `job_not_ready` | 404 | The playlist has not finished converting (nothing to download or import). |
| `job_running` | 409 | The playlist is still converting. |
| `playlist_incomplete` | 409 | The M3U points at files that are no longer on disk; confirm to accept it. |
| `media_player_unavailable` | 404 | The platform media player or its library cannot be reached. |
| `nothing_to_import` | 409 | The playlist has no resolved tracks to hand over. |
| `media_player_failed` | 502 | The handoff itself failed. |
| `not_found` | 404 | No such API endpoint (or track, or cached image). |
| `method_not_allowed` | 405 | Wrong HTTP method for an endpoint. |

## Endpoints

### Application

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/meta` | Version, media-player and media-library availability, fast-mode default, upload limits. |
| `GET` | `/api/update` | Whether a newer release is available; never fails on the network. |
| `GET` | `/api/icon.png` | The canonical application icon. |

### Import and browse

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/upload` | Import an Exportify ZIP as `multipart/form-data` with a `file` part. |
| `POST` | `/api/upload/picked` | Import the ZIP the desktop shell picked in the OS file dialog (no body; the path stays in the shell). |
| `GET` | `/api/jobs/<job_id>` | The import's playlists, current selection, download folder, and defaults. |
| `GET` | `/api/jobs/<job_id>/playlists/<playlist_id>` | One playlist with its tracks in export order. |
| `PUT` | `/api/jobs/<job_id>/selection` | Store the selection: `{"playlist_ids": ["0", "1"]}`. |

Uploading sets the session the job routes are scoped to, exactly like the
Jinja upload does. A playlist id is the playlist's position in the export
(`"0"`, `"1"`, ...), stable for the life of the upload.

### Convert

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/jobs/<job_id>/processing` | Start converting the selection: `{"playlist_ids": ["1"], "fast_mode": false}`. Both keys are optional; without them the stored selection and the `[fast] enabled` default apply. Answers `202` with the batch summary. |
| `GET` | `/api/jobs/<job_id>/processing` | Live progress for the selection, ready to poll every second. `?playlist_ids=0,2` scopes it. |
| `GET` | `/api/jobs/<job_id>/playlists/<playlist_id>/processing` | Live progress for one playlist (one `ProcessingJob.snapshot()` with per-track artwork flags). |
| `POST` | `/api/jobs/<job_id>/playlists/<playlist_id>/processing/retry` | Re-resolve the tracks with no usable file; answers the state plus `retried` (0 when nothing was left to do). |
| `GET` | `/api/jobs/<job_id>/playlists/<playlist_id>/result` | The finished outcome of one playlist, with per-track lyrics and `m3u_url`. |
| `GET` | `/api/jobs/<job_id>/result` | The outcome of every selected playlist, for the batch result screen (`409 job_running` until all of them finish). |

Starting also stores the effective selection, so the status and result routes -
and the still-served Jinja pages - find the same playlists afterwards. Each
playlist gets its own M3U (`playlist-<id>.m3u` inside the download folder), so
converting a batch never overwrites another playlist's file.

### Output

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/jobs/<job_id>/playlists/<playlist_id>/m3u` | Download the generated playlist (served as an attachment). |
| `GET` | `/api/jobs/<job_id>/playlists/<playlist_id>/artwork/<int:index>` | Cached artwork for one track, without network access. |
| `POST` | `/api/jobs/<job_id>/playlists/<playlist_id>/media-player` | Hand one playlist to the platform media player; answers the state plus `media_player`. |
| `POST` | `/api/jobs/<job_id>/media-player` | Import every selected playlist into the media library as its own playlist. |

`m3u` answers `409 playlist_incomplete` with `missing`, `total`, and
`confirm_url` when the playlist references files that are gone; requesting that
URL serves the playlist anyway. Artwork answers `404 not_found` for a track
that has no cached image, is out of range, or whose download was deleted by
hand.

## Not here yet

The settings screen does not exist in the current application (it is task 118),
so no settings endpoints exist either; `/api/meta` covers the read-only
defaults the shell needs today. Nothing under `/api` renders HTML, and the
Jinja routes are untouched until the migration removes them.
