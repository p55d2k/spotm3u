# Spotify → Local M3U

## Project Goal

Build a local Flask web app that converts Spotify playlists exported through Exportify into local `.m3u` playlists referencing audio files already present on the user's computer.

The application must work with Spotify Free and must not require Spotify Premium or the Spotify Web API.

## User Workflow

1. Open the Flask app.
2. Follow instructions to open Exportify.
3. Complete Spotify authentication through Exportify.
4. Use Exportify's "Export All" function.
5. Download the resulting ZIP.
6. Return to the Flask app.
7. Upload the ZIP.
8. The app parses the exported playlists.
9. User selects a playlist.
10. App matches tracks against the user's local music library.
11. App reports matched, missing, and ambiguous tracks.
12. App generates an M3U playlist.
13. User downloads the M3U.

## Hard Constraints

- Spotify authentication happens entirely through Exportify.
- Never ask the user for their Spotify password.
- Do not use the Spotify Web API.
- Do not require Spotify Premium.
- Do not scrape Spotify's website.
- Do not automate Spotify's browser UI.
- Do not reintroduce the old Selenium/Playwright/DOM-scraping architecture.
- Exportify-specific parsing must remain separate from generic playlist/audio/M3U logic.
- Preserve playlist order.
- Preserve intentional duplicate playlist entries.
- Never silently choose an ambiguous local audio match.
- Uploaded ZIP files are untrusted input.
- Never execute uploaded files.
- Do not log credentials, secrets, or sensitive authentication data.

## Architecture

```text
Exportify ZIP
      ↓
Exportify Parser
      ↓
Playlist / Track Models
      ↓
Local Audio Resolver ──── online source search (new path when no local match)
      ↓                           ↓
ResolvedTrack               Source Candidate Ranking
      ↓                       (identity first, then source quality)
Metadata / Artwork            ↓
Enrichment                    source validation
      ↓                       ↓
Final verified MP3 ──── yt-dlp download
      ↓                       ↓
M3U Writer             downloaded-audio validation
      ↓                       ↓
M3U Download          ResolvedTrack ──→ Metadata / Artwork Enrichment ──→ M3U Writer
```

## Technology

- Python 3.13
- Flask
- Jinja2
- HTML/CSS
- Minimal vanilla JavaScript
- pathlib
- zipfile
- csv / json
- mutagen
- rapidfuzz
- pytest

## Code Principles

- Keep Flask routes thin.
- Put business logic in reusable modules.
- Prefer simple synchronous request/response behavior unless the project genuinely needs background processing.
- Inspect real input formats before making assumptions.
- Reuse existing working code where appropriate.
- Do not rewrite unrelated parts of the project.
- Add tests for new non-trivial behavior.
- Keep interfaces between modules explicit.

## Documentation Map

Read only the documentation relevant to the current task.

- `docs/architecture.md` → project structure, data flow, module boundaries
- `docs/artwork.md` → album artwork lookup priority, sources, caching, fallbacks
- `docs/exportify.md` → Exportify ZIP format and parsing
- `docs/web.md` → Flask routes, templates, sessions, UI flow
- `docs/matching.md` → local music discovery and track matching
- `docs/m3u.md` → M3U generation and playlist semantics
- `docs/security.md` → uploads, temporary files, validation, cleanup

## pytest

Run pytest with `uv run pytest`.
