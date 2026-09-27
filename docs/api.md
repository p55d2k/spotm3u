# JSON API

The React frontend talks to SpotM3U exclusively through `/api/...` routes
(`src/spotm3u/api.py`). They are a thin layer over the shared job helpers in
`src/spotm3u/web_jobs.py`, so no matching, download, metadata, or lyrics logic
lives in the HTTP layer.

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
| `preference_invalid` | 400 | The preference body is empty, names an unknown preference, or carries a value that preference does not accept. |
| `history_invalid` | 400 | A history filter or paging argument names something the history does not support. |
| `history_not_found` | 404 | No stored run has that id. |
| `history_unavailable` | 503 | The processing history is off, or its local file cannot be read. |
| `clear_invalid` | 400 | The clear names nothing to delete, an unknown item, or arrives without the confirmation phrase. |
| `clear_refused` | 409 | The clear would reach the music library itself, or a conversion is running. |
| `clear_failed` | 500 | The files could not be deleted. |
| `not_found` | 404 | No such API endpoint (or track, or cached image). |
| `method_not_allowed` | 405 | Wrong HTTP method for an endpoint. |

## Endpoints

### Application

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/meta` | Version, media-player and media-library availability, fast-mode default, upload limits. |
| `GET` | `/api/update` | Whether a newer release is available; never fails on the network. |
| `GET` | `/api/preferences` | The stored UI preferences (`{"theme": "dark"}`); `{}` when nothing is stored yet. |
| `PUT` | `/api/preferences` | Store a UI preference: `{"theme": "light"|"dark"}`. Answers with what is stored afterwards. |
| `GET` | `/api/icon.png` | The canonical application icon. |

The theme is the only preference that exists so far: it is stored by the
application (see [web.md](web.md#theme)) because the desktop window cannot keep
WebView storage, and the stored value is rendered into the served shell as
`<html data-theme="...">`. An unsupported theme, an unknown preference name, or
an empty body answers `400 preference_invalid`; a write that cannot happen
answers `200` with the unchanged stored state rather than claiming success.

### Import and browse

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/upload` | Import an Exportify ZIP as `multipart/form-data` with a `file` part. |
| `POST` | `/api/upload/picked` | Import the ZIP the desktop shell picked in the OS file dialog (no body; the path stays in the shell). |
| `GET` | `/api/jobs/<job_id>` | The import's playlists, current selection, download folder, and defaults. |
| `GET` | `/api/jobs/<job_id>/playlists/<playlist_id>` | One playlist with its tracks in export order. |
| `PUT` | `/api/jobs/<job_id>/selection` | Store the selection: `{"playlist_ids": ["0", "1"]}`. |

Uploading sets the session the job routes are scoped to. A playlist id is the
playlist's position in the export
(`"0"`, `"1"`, ...), stable for the life of the upload.

### Convert

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/jobs/<job_id>/processing` | Start converting the selection: `{"playlist_ids": ["1"], "fast_mode": false}`. Both keys are optional; without them the stored selection and the `[fast] enabled` default apply. Answers `202` with the batch summary. |
| `GET` | `/api/jobs/<job_id>/processing` | Live progress for the selection, ready to poll every second. `?playlist_ids=0,2` scopes it. |
| `GET` | `/api/jobs/<job_id>/playlists/<playlist_id>/processing` | Live progress for one playlist (one `ProcessingJob.snapshot()` with per-track artwork flags). |
| `POST` | `/api/jobs/<job_id>/playlists/<playlist_id>/processing/retry` | Re-resolve the tracks with no usable file; answers the state plus `retried` (0 when nothing was left to do) and `tracks`, the indices that were picked up. |
| `GET` | `/api/jobs/<job_id>/playlists/<playlist_id>/result` | The finished outcome of one playlist, with per-track lyrics and `m3u_url`. |
| `GET` | `/api/jobs/<job_id>/result` | The outcome of every selected playlist, for the batch result screen (`409 job_running` until all of them finish). |

Starting also stores the effective selection, so the status and result routes
find the same playlists afterwards. Each
playlist gets its own M3U (`playlist-<id>.m3u` inside the download folder), so
converting a batch never overwrites another playlist's file.

The retry route takes an optional body naming the tracks to retry:

- No body (or `{}`): every track that needs it is retried, which is what the
  "Retry N unresolved tracks" action posts.
- `{"tracks": [3, 7]}`: exactly those tracks, which is how one track or a ticked
  set is retried. The indices are the positions in the playlist, the same ones
  the track rows and the artwork route use.

A track whose audio is still on disk is finished, not retryable: naming one is
`400 track_not_retryable` rather than downloading a second copy of it. A body
that is not an object, a `tracks` that is not a list of integers, an empty
list, a repeat, or an index outside the playlist is `400 retry_invalid` — a
retry is refused as a whole rather than going ahead with part of the selection.
A retry while the playlist is still converting is `409 job_running`, the same as
the result route.

A retried track keeps the reason it failed with while it waits, so the interface
can say why it is queued again; the outcome of the attempt it replaces is kept
in the history. Each track state also reports `attempts`, how many times it has
been through the pipeline, so a row can show "Retried 1×". Sources a previous
attempt turned down are withheld from the next attempt rather than being tried
and refused again.

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

### Download history

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/history` | One page of stored conversions. `?status=`, `?q=`, `?order=recent\|oldest\|name`, `?limit=`, `?offset=`. |
| `GET` | `/api/history/<int:run_ref>` | One stored conversion with its tracks, annotated with what is still on disk. |

Both routes read the local processing history and never change it; see
[architecture](architecture.md#processing-history) for the state model and where
the file lives. `status` takes one of `queued`, `processing`, `completed`,
`failed`, `cancelled` or `skipped` and selects the runs in that state *and* the
runs holding a track in it, so a finished conversion with a skipped track is
still found under `skipped`. `q` matches the playlist name, a track title or a
track artist; `limit` is clamped to 1-500 and `offset` to 0 or more. An
unrecognised value answers `400 history_invalid` rather than being ignored, and
the list sends its own `statuses` along so the filter can only offer states the
model has.

The list answers with the runs, the `total` that matched the filters (not the
page), the paging that was applied, and the states:

```json
{
  "runs": [
    {
      "id": 1,
      "job_id": "a1b2c3",
      "playlist_id": "0",
      "playlist_name": "Road trip",
      "status": "completed",
      "total_tracks": 2,
      "counts": { "queued": 0, "processing": 0, "completed": 1, "failed": 1, "cancelled": 0, "skipped": 0 },
      "error": "",
      "m3u_path": "/Users/me/Music/playlist-0.m3u",
      "output_dir": "/Users/me/Music",
      "fast_mode": false,
      "started_at": 1750000000000,
      "finished_at": 1750000060000
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0,
  "order": "recent",
  "statuses": ["queued", "processing", "completed", "failed", "cancelled", "skipped"]
}
```

The detail route answers the same fields plus `tracks`, each in playlist order,
with its identity, the state, the stage it reached, the resolution, the reason
and error text, the source, the output path, `retry_count`, its three
timestamps, and `file_missing` for a completed track whose audio has since been
deleted. A run the application was closed in the middle of is recorded as
`cancelled` with an error saying so, rather than claiming to still be running.

Each track also carries `attempts`, every attempt at it from the first onwards
(oldest first, the last one being the track's current state). A retry adds an
attempt rather than replacing the last, so the record of what went wrong the
first time survives a later success. Each attempt reports its own number,
state, stage, resolution, reason, error, source, output path, and its three
timestamps. An attempt that was still open when the application was closed is
recorded as `cancelled`, which is how a run interrupted mid-conversion reads.

A history written before attempts were kept is backfilled on first open: each
stored track is given the one attempt that is still known, numbered with the
retry count it reached. The attempts it lost cannot be recovered, which is why
`retry_count` and the attempt list are reported separately rather than one being
derived from the other.

With `[history] enabled = false`, or when the local file cannot be read, both
routes answer `503 history_unavailable`: the record is off, not empty.

### Developer

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/developer/inventory` | What a clear would remove right now, per item, with sizes and the confirmation phrase. |
| `POST` | `/api/developer/clear` | Delete the chosen items: `{"items": ["songs"], "confirm": "delete"}`. |

The items are `songs` (the MP3s in the download folder), `playlists` (the `.m3u`
files beside them), `manifest` (the download record, so those songs are fetched
again), `artwork` (the artwork cache), `lyrics` (the `.lrc` sidecars) and
`uploads` (the temporary import folders). Each is independent; the answer lists
what each one removed plus `bytes_freed`. The developer panel shows this
inventory in a **Clear downloaded data** dialog, one checkbox per item with its
current size.

Only files SpotM3U wrote are deleted: the songs, playlists and manifest directly
inside the download folder, and the two cache folders inside that same folder.
Nothing recurses out of them, no symlink is followed, and a download folder that
resolves to the music library itself (or to a folder above it) answers
`409 clear_refused` instead of being emptied. A conversion that is still running
answers `409 clear_refused` too, because that job is writing into the same
folders.

Deletion is permanent, so the API requires the phrase the inventory reported -
not only the panel. Like the rest of `/api` it has no authentication of its own,
which is why the action is limited to files this application created.

## Not here yet

The application has no settings screen, so `/api/preferences` covers only the
theme; `/api/meta` covers the read-only defaults the shell needs today. The
history is read-only over the API - it is written by the processing job, not by
an endpoint - and nothing under `/api` renders HTML.
