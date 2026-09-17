# Exportify Integration

## Purpose

Exportify is the source of Spotify playlist metadata.

The Flask application does not authenticate with Spotify and does not communicate with Spotify directly.

The user authenticates through Exportify, exports their playlists, downloads the ZIP, and uploads it to this application.

---

## Required Workflow

```text
User
 ↓
Exportify
 ↓
Spotify login
 ↓
Export All
 ↓
ZIP download
 ↓
Flask upload
 ↓
Exportify parser
```

The application must never ask the user for their Spotify password.

---

## Important Parsing Rule

Do not assume the structure of an Exportify export from memory.

Before implementing or changing the parser:

1. Inspect an actual Exportify ZIP.
2. Identify all relevant files.
3. Inspect headers/fields.
4. Determine how playlists are represented.
5. Determine how tracks are represented.
6. Determine how ordering is represented.
7. Determine how duplicate tracks are represented.
8. Check encoding and Unicode behavior.
9. Check whether multiple export formats exist.

Parser behavior should be based on the actual input.

---

## Parser Boundary

The parser converts Exportify-specific data into generic models:

```text
Exportify files
      ↓
Exportify parser
      ↓
Playlist
Track
```

Code outside `exportify/` should not need to know about Exportify's CSV column names, filenames, or internal structure.

---

## Playlist Requirements

For every playlist, preserve:

- playlist name
- stable identifier where available
- track order
- track count where available
- track entries
- intentional duplicates

---

## Track Requirements

Extract where available:

- title
- artist(s)
- album
- duration
- Spotify track ID
- Spotify URL

Missing fields should become `None` or an appropriate empty value rather than causing unnecessary failure.

---

## Unicode

Exports may contain:

- accented characters
- CJK characters
- emoji
- non-Latin artist names
- punctuation from different Unicode ranges

Use Unicode-safe parsing and retain the original values.

---

## Malformed Input

The parser should distinguish between:

- completely invalid export
- missing expected files
- malformed individual rows
- missing optional fields
- empty playlists
- empty exports

Do not silently produce incorrect playlist data.

---

## Order and Duplicates

Track order is significant.

If a playlist contains:

```text
A
B
A
C
```

the parsed playlist must contain:

```text
A
B
A
C
```

Do not deduplicate tracks merely because they have the same Spotify ID.

Only remove duplicates when there is strong evidence that the duplicate is an artifact of parsing rather than an actual playlist entry.
