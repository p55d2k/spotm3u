# Web Application

## Technology

Use Flask with:

- Jinja2 templates
- vanilla HTML/CSS/JS unless existing project requirements dictate otherwise

## User Flow

### 1. Homepage

Explain that the user should:

1. Open Exportify.
2. Log into Spotify there.
3. Export their playlists.
4. Download the resulting ZIP.
5. Upload the ZIP to this application.

The application does not handle the Spotify login.

### 2. ZIP Upload

The user uploads the Exportify ZIP.

The backend:

- validates the upload
- extracts it into a controlled job directory
- parses playlists

### 3. Playlist Selection

Display playlists discovered from the Exportify export.

The user selects one.

### 4. Processing

The selected playlist is processed.

For each track:

Local resolver
→ online search if needed
→ source validation
→ yt-dlp download
→ downloaded-audio validation

The UI should expose progress.

### 5. Result

Display:

- total tracks
- successful tracks
- local matches
- downloaded tracks
- failed tracks
- ambiguous tracks
- rejected/uncertain tracks

Provide the generated M3U when available.

## Important

The web UI must never imply that a track was successfully resolved when only a weak/uncertain source was found.

## Job State

Processing should use a job abstraction rather than performing a large playlist synchronously inside a single HTTP request.

The frontend can poll a job-status endpoint or use another lightweight progress mechanism.

## Security

Client requests must never directly provide arbitrary filesystem paths.

Job IDs should resolve only to internal job directories.
