# M3U Generation

## Purpose

Generate an M3U playlist containing the local audio files resolved for an Exportify playlist.

## Input

The M3U writer receives an ordered sequence of resolved local audio paths.

A resolved path may originate from:

1. an existing local audio file, or
2. an MP3 downloaded through yt-dlp.

## Output

The output is a `.m3u` file.

The M3U references local audio files.

Relative entries use forward slashes, including when the application runs on
Windows, so playlists remain portable across players and platforms.

It does not reference remote source URLs.

It does not perform downloading.

## Order

Preserve the order from Exportify.

## Duplicates

Do not globally deduplicate playlist entries.

If the original playlist contains:

A
B
A

the M3U should contain:

A
B
A

The physical file may be shared between both A entries.

## Unresolved Tracks

Tracks that are:

- missing
- ambiguous
- rejected
- failed
- uncertain

must not silently appear as successful entries.

The processing result should report them separately.

## Verification Before Serving

The written playlist is checked when it is handed out, not when it is written:
the application reads the file back, resolves each entry (relative entries
against the playlist's own directory) and reports the ones that are no longer on
disk. The user is warned and can download anyway or re-download the missing
tracks, so a playlist never silently reaches a media player with entries that
resolve to nothing.

This check belongs to the serving step. The writer still only writes, and it is
never asked to rewrite a playlist after the fact.

## Separation of Concerns

The M3U writer must not:

- search online
- match audio
- call yt-dlp
- validate source URLs
- validate audio content

Its only job is writing the playlist.
