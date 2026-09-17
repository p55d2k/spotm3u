# Web Application

## Purpose

The Flask application provides the user interface for importing an Exportify ZIP, selecting a playlist, matching tracks, and downloading an M3U file.

---

## User-Facing Flow

```text
Homepage
   ↓
Exportify instructions
   ↓
Upload ZIP
   ↓
Playlist selection
   ↓
Processing
   ↓
Results
   ↓
M3U download
```

---

## Routes

The exact route structure may evolve, but the application should have concepts equivalent to:

```text
GET  /
POST /upload
GET  /playlists/<job_id>
POST /playlists/<job_id>/select
GET  /processing/<job_id>
GET  /result/<job_id>/<playlist_id>
GET  /download/<job_id>/<playlist_id>
```

Routes should remain thin and delegate business logic to backend modules.

---

## Homepage

Explain:

1. Go to Exportify.
2. Log into Spotify there.
3. Press "Export All".
4. Download the ZIP.
5. Upload the ZIP here.

Clearly state that the application does not require the user's Spotify password.

---

## Upload Page

The upload form should:

- accept ZIP files
- show useful instructions
- display validation errors
- avoid exposing server filesystem paths
- provide a clear next step

---

## Playlist Page

Display:

- playlist name
- track count
- selection control

Handle:

- empty exports
- no playlists
- expired jobs
- invalid playlist IDs

---

## Processing Page

The initial implementation should prefer simple synchronous processing.

Do not introduce:

- WebSockets
- Redis
- Celery
- background workers

unless real processing time demonstrates that they are necessary.

---

## Result Page

Show:

- playlist name
- total tracks
- matched count
- missing count
- ambiguous count
- output status
- download control

Problematic tracks should be identifiable.

---

## Sessions

The browser session should contain only small values such as:

```text
job_id = abc123
```

Do not put:

- ZIP contents
- complete playlist objects
- complete track lists
- local library indexes

into the cookie session.

Large state belongs on the server.

---

## Errors

Expected errors should render user-friendly pages/messages rather than raw tracebacks.

Examples:

- invalid ZIP
- unsupported upload
- empty export
- expired job
- invalid playlist
- matching failure
- missing generated output

Unexpected errors should be logged while exposing only a safe message to the user.

---

## Download

Downloads must refer to generated files through server-controlled identifiers.

Never construct a filesystem path directly from an arbitrary user-supplied path.
