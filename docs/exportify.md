# Exportify

SpotM3U gets its playlist data from [Exportify](https://exportify.net/): you
authenticate with Spotify inside Exportify, export your playlists, and download
the resulting ZIP. SpotM3U never talks to Spotify directly — it does not use
the Spotify Web API, it does not scrape Spotify's site, and it never asks for
your Spotify password. Authentication happens entirely in Exportify; SpotM3U
only ever receives the downloaded ZIP.

```text
User
 ↓
Exportify — Spotify authentication and "Export All"
 ↓
ZIP download
 ↓
SpotM3U upload
 ↓
Exportify parser
```

## What the parser produces

The upload is an **untrusted ZIP** (see the [security notes](security.md)), and
the parser only reads it — uploaded files are never executed. The parser
converts Exportify's files into the generic `Playlist` and `Track` models used
by the rest of the application, so nothing outside the parser needs to know
about Exportify's CSV column names, filenames, or internal structure.

For every playlist it preserves:

- playlist name
- a stable identifier where Exportify provides one
- track order
- track count where available
- track entries, including **intentional duplicates**
- every available track field: title, artist(s), album, duration, Spotify
  track ID, and Spotify URL

Missing fields become empty values rather than failing the whole export.

## Order and duplicates

Track order is significant and is preserved exactly. A playlist that contains
`A B A C` stays `A B A C`: two entries with the same Spotify ID are two entries,
not one. Tracks are only de-duplicated when there is strong evidence that the
duplicate is an artifact of the parsing itself rather than an actual playlist
entry.

## Unicode

Exports regularly contain accented characters, CJK text, emoji, and
non-Latin artist names. The parser is Unicode-safe and keeps the original
values as they were exported.

## Handling malformed exports

The parser distinguishes between a completely invalid export, missing expected
files, malformed individual rows, missing optional fields, and empty playlists
or exports. It never silently produces incorrect playlist data — a damaged
export is reported rather than guessed at.