# Architecture

## Overview

The application converts Spotify playlist metadata exported through Exportify into an M3U playlist pointing to local audio files.

The important architectural boundary is:

```text
Exportify ZIP
      ↓
Spotify/Exportify-specific code
      ↓
Generic Playlist + Track models
      ↓
Generic local audio resolver
      ↓
Generic M3U writer
```

Spotify-specific knowledge should stop at the parser.

---

## Project Structure

```text
spotify-m3u/
├── pyproject.toml
├── README.md
├── context.md
├── tasks.md
├── docs/
│   ├── architecture.md
│   ├── exportify.md
│   ├── web.md
│   ├── matching.md
│   ├── m3u.md
│   └── security.md
├── src/
│   └── spotm3u/
│       ├── __init__.py
│       ├── app.py
│       ├── models.py
│       ├── config.py
│       ├── exportify/
│       │   ├── __init__.py
│       │   └── parser.py
│       ├── audio/
│       │   ├── __init__.py
│       │   ├── resolver.py
│       │   └── local.py
│       ├── m3u/
│       │   ├── __init__.py
│       │   └── writer.py
│       └── utils/
│           ├── __init__.py
│           └── normalize.py
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── upload.html
│   ├── playlists.html
│   ├── processing.html
│   └── result.html
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── app.js
├── tests/
│   ├── test_models.py
│   ├── test_exportify.py
│   ├── test_resolver.py
│   ├── test_m3u.py
│   └── test_routes.py
└── uploads/
```

---

## Data Models

### Playlist

```python
@dataclass
class Playlist:
    id: str | None
    name: str
    track_count: int | None = None
    tracks: list["Track"] = field(default_factory=list)
```

### Track

```python
@dataclass
class Track:
    title: str
    artists: list[str]
    album: str | None
    duration_ms: int | None
    spotify_id: str | None = None
    spotify_url: str | None = None
```

### ResolvedTrack

```python
@dataclass
class ResolvedTrack:
    track: Track
    local_path: Path
```

Additional matching/result models may be introduced if they make the interfaces clearer.

---

## Module Responsibilities

### `app.py`

Responsible for:

- Flask routes
- request validation
- coordinating services
- rendering templates
- redirects
- HTTP responses

It should not contain substantial parsing, matching, or M3U business logic.

### `models.py`

Contains shared application data structures.

### `exportify/parser.py`

Responsible only for:

- understanding Exportify's exported format
- reading export files
- converting them into generic models

No local audio matching should occur here.

### `audio/local.py`

Responsible for:

- discovering local audio files
- reading local metadata
- representing local library entries

### `audio/resolver.py`

Responsible for:

- comparing `Track` metadata against local audio
- exact matching
- normalized matching
- fuzzy matching
- returning match results

### `m3u/writer.py`

Responsible only for generating M3U output from resolved tracks.

### `utils/normalize.py`

Contains reusable normalization functions.

---

## Job Storage

Temporary state should be stored server-side.

Example:

```text
/tmp/spotm3u/
└── job-abc123/
    ├── export.zip
    ├── extracted/
    ├── state.json
    └── output/
        └── playlist.m3u
```

The browser should retain only a small opaque job identifier.

Do not put complete playlist data or uploaded file contents into the Flask cookie session.

---

## Processing Flow

```text
GET /
    ↓
Exportify instructions
    ↓
POST /upload
    ↓
Validate ZIP
    ↓
Create job
    ↓
Extract safely
    ↓
Parse Exportify
    ↓
GET /playlists/<job_id>
    ↓
User selects playlist
    ↓
Resolve local tracks
    ↓
Generate M3U
    ↓
GET /result/<job_id>/<playlist_id>
    ↓
GET /download/...
```

---

## Design Principle

The application should remain usable even if Exportify is eventually replaced.

The generic layers should know nothing about where the playlist metadata originated.
