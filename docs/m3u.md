# M3U playlists

When a playlist has been resolved, SpotM3U writes an `.m3u` file referencing
the local audio files that matched — existing files from your library plus any
MP3s downloaded online. The generator writes the playlist and nothing else: it
does not search online, match audio, run downloads, or validate source URLs.

## What ends up in the file

- Entries are local audio paths only. Remote source URLs are never written.
- Playlist order from Exportify is preserved exactly.
- Duplicate entries are kept: a playlist that lists the same song twice gets
  two M3U entries (both may share the same physical file).
- Relative entries use forward slashes even on Windows, so playlists stay
  portable across players and platforms.
- Unresolved tracks — missing, ambiguous, rejected, failed, or uncertain — are
  reported on the result page and never silently appear as successful entries.

## Verification before download

SpotM3U does not just hand out the file: when you download the playlist, it
re-reads it and checks that every referenced file still exists on disk,
resolving relative entries against the playlist's own directory. If audio was
deleted by hand, you are warned and can either re-download the missing tracks
or download the playlist anyway. A playlist never reaches your media player
silently with entries that point at nothing.

This check belongs to the serving step only. The writer is never asked to
rewrite a playlist after the fact.