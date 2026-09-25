# Downloads

Tracks that cannot be resolved from your existing local library can be
downloaded online with `yt-dlp` (using `ffmpeg` to extract audio into MP3) and
stored as local files, so they can then be referenced by the generated M3U like
any other local audio.

The download pipeline for a track is:

```text
Track
 → source search (all queries aggregated)
 → candidate ranking (identity first, then source quality)
 → source validation
 → yt-dlp download → MP3
 → downloaded-audio validation
 → resolved local file
```

Selection happens **after** every search query has been run and ranked. The
highest-ranked plausible candidate is downloaded first, and an official music
video is used only as a fallback when no audio/lyrics source exists for the same
recording. Validation is deliberately not so strict that every imperfect
candidate is rejected before downloading — a plausible candidate may need to be
downloaded before its actual audio quality can be judged.

## Where files are stored

Downloads and the generated M3U go into the download directory, which defaults
to `<MUSIC_LIBRARY>/SpotM3U/` and can be changed in `config.toml`. This is a
persistent location, not a temporary job folder, so the M3U keeps working and
downloaded files can be matched by the local resolver on later runs instead of
being downloaded again.

Earlier releases used `<MUSIC_LIBRARY>/SpotM3U-downloads/`. A folder left under
that name is renamed to `SpotM3U` the first time a download directory is
resolved, so downloads from older versions keep working. The migration only
applies to the default location (an explicit `download_dir` is used exactly as
configured), never merges folders (if the new one already exists, the old one
is left untouched), and on a failed rename the legacy folder is kept in place.

## Fast mode

**Fast mode** is a lightweight path for large playlists: it finds audio,
downloads it as MP3, and writes the playlist — nothing else. Compared with the
normal pipeline it skips source validation, download validation, candidate
retry beyond the first search's results, the download cache, and all metadata
work (ID3 tags, album cover, artist image, lyrics, and their lookups). It
keeps playlist parsing, local-library matching, source searching, the download
itself, filename sanitization, M3U generation, and progress reporting.

It is much faster because the expensive per-track work is network round trips:
6 parallel searches become 1, validation passes are removed, and no
artwork/lyrics requests happen at all. The trade-off is confidence: a fast-mode
match can be a live version, a cover, or a wrong artist, and the MP3s carry no
tags, artwork, or lyrics. A track that finds no usable source fails immediately
rather than falling through into the normal pipeline.

Enable fast mode globally with `[fast] enabled = true` in `config.toml`, or per
run with the **Fast mode** toggle on the processing page.

## Candidate retry

When several plausible candidates exist, SpotM3U downloads the highest-ranked
one, validates the resulting audio, and on a failure tries the next plausible
candidate, stopping when a valid recording is found or candidates are
exhausted. Obviously unsuitable candidates are never retried.

## Download discipline

The downloader treats source URLs as data, never as shell commands, and keeps
all temporary `yt-dlp`/`ffmpeg` files inside a controlled job directory.
Filenames are filesystem-safe and deterministic, and raw metadata is never used
as a shell command.

Concurrency is bounded:

- track-level download concurrency (`download.workers`) is a separate, smaller
  cap than search concurrency (`web.resolve_workers`), so a large playlist can
  search broadly without launching an uncontrolled number of downloads
- `yt-dlp`/`ffmpeg` work stays inside the job's bounded worker pool
- each download has a wall-clock timeout (`download.timeout`), search and
  download sockets have network timeouts, and retries are capped — a hung
  source or a failing download fails instead of occupying a worker forever

A successful `yt-dlp` run alone does not mean the track resolved: the resulting
audio must pass validation before it can be included in the final playlist.
Cinematic and instrumental files may contain ambience, impacts, or vocal-like
textures — these are warnings unless there is strong evidence of actual spoken
dialogue, and a downloaded vocal version of an explicitly instrumental target
fails validation and triggers candidate retry.

### FFmpeg

Audio extraction and conversion need `ffmpeg`/`ffprobe`. Source runs look for
them on `PATH`; the standalone releases bundle them. A packaged bundle can also
use an `ffmpeg/` directory beside the executable as an override (see the
[troubleshooting notes](troubleshooting.md)).

## Embedded metadata

What gets written into a generated MP3 is configurable, so a library that only
wants audio can keep files as small as possible:

- `[metadata] enabled` (default `true`) — master switch. When `false`, nothing
  is embedded and no lookup or download runs for it: no text tags, album cover,
  artist image, or lyrics.
- `[metadata] tags` (default `true`) — standard ID3 text fields (title,
  artists, album, album artist, track/disc number, year, genre, comment).
- `[artwork] album_artwork` (default `true`) — the album front cover. Embedded
  covers are by far the largest part of the metadata, so this is the biggest
  space saving.
- `[artwork] artist_artwork` (default `true`) — the artist profile image (see
  [artwork](artwork.md)).
- `[lyrics] enabled` (default `true`) — embed lyrics (see below).

Disabling an option also skips its network lookups, saving time as well as
space. Filenames, M3U generation, and track resolution are unaffected — a track
is never marked failed because metadata embedding is off. Fast mode skips
metadata enrichment altogether.

## Lyrics

After a track resolves, its lyrics are written into the file's ID3 metadata.
Lyrics are retrieved through the `syncedlyrics` library, which searches public
lyrics providers itself. SpotM3U performs no web search of its own, scrapes no
lyrics sites, and hardcodes no provider URLs or parsers.

**Synced lyrics are preferred.** The library looks for timed lyrics first and
falls back to plain text, so a track gets the best form a provider has. When
the lyrics are timed, they are parsed once into structured timestamp/text pairs
and written as two ID3 frames: the plain `USLT` frame holds only the clean,
timestamp-free text (Apple Music reads this and would otherwise render the LRC
tags literally), and the `SYLT` frame holds the same lines with their
millisecond timing for players that support synchronized lyrics. Apple Music
may ignore `SYLT` for imported local files, which is expected. An `.lrc` file
is also written next to the audio file (`song.mp3` → `song.lrc`) for players
that read a sidecar instead of ID3 frames. Plain lyrics go into `USLT` only —
there is nothing to time, so no `SYLT` and no sidecar. Deezer is not a lyrics
source: its public API has no lyrics endpoint.

Lyrics are optional enrichment and never affect resolution:

- a track with no lyrics keeps empty lyrics fields
- an unavailable provider or a network failure is logged and ignored
- a malformed or empty library result is discarded
- an audio file that cannot hold the field is left untouched
- a sidecar that cannot be written only costs the sidecar, not the embedded
  lyrics
- existing metadata and embedded artwork are preserved
- an `.lrc` from an earlier run is never deleted

The [result page](web.md) labels each track **Synced lyrics** or **Plain
lyrics** (nothing when the file has no lyrics), read from the file itself.

Importing into Apple Music is a separate, **experimental and opt-in** matter:
Music ignores embedded `SYLT` for local files, and writing Apple's own catalog
id may or may not make it supply its lyrics. See
[Apple Music catalog IDs](apple-music.md).

## Cache

Successful downloads may be cached and reused when their association with the
requested recording is strong. The cache never relies on filenames alone — a
cached entry is only reused while its audio file is still on disk (the manifest
lives in the download folder next to it), and the manifest heals itself:
entries whose file has been deleted are dropped on the next lookup or store, so
a stale row can never make SpotM3U skip a re-download. A finished run reports
tracks whose file disappeared as **missing from disk** and offers a retry.

Concurrent downloaders targeting the same output file (for example two batch
playlists that share a song) are single-flighted: the first caller downloads,
and the others wait and reuse that validated file instead of downloading it
again. Reuse only happens when the file passes audio validation against the
waiting recording; otherwise the caller downloads its own copy.