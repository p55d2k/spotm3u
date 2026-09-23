# Downloads

## Purpose

The project downloads online sources for tracks that cannot be resolved from the existing local audio library.

Downloaded audio becomes local MP3 files that can be referenced by the generated M3U.

## Download Directory

Downloads and the generated M3U are written to `DOWNLOAD_DIR`, which defaults
to `<MUSIC_LIBRARY>/SpotM3U/`.

Earlier releases used `<MUSIC_LIBRARY>/SpotM3U-downloads/`. A folder left under
that name is renamed to `SpotM3U` the first time a download directory is
resolved, so downloading after an upgrade keeps using the files that are
already there. Three details of that migration:

- it only applies to the default location — an explicit `download_dir` is used
exactly as configured and never moved
- nothing is merged: when `<MUSIC_LIBRARY>/SpotM3U/` already exists the legacy
  folder is left untouched, and the new folder is used
- a rename that fails (a read-only or cross-device music library) leaves the
  legacy folder in place and uses it, so existing downloads stay visible

This is a persistent location, not a temporary job folder, so the M3U keeps
working and downloaded files can be matched by the local resolver on later
runs instead of being downloaded again.

## Pipeline

Track
→ source search (all queries aggregated)
→ candidate ranking (identity first, then source quality)
→ recording/version validation (including instrumental vs vocal)
→ source validation
→ yt-dlp
→ MP3
→ downloaded-audio validation
→ resolved local file

## Important

Source validation should not be so strict that every imperfect candidate is rejected before downloading.

A plausible candidate may need to be downloaded before the application can determine whether its actual audio is suitable.

Selection always happens **after** all search queries have been run and
ranked. The highest-ranked plausible candidate is downloaded first; an
official music video is only used as a fallback when no audio/lyrics source
for the same recording exists.

## Fast Mode

Fast mode is the lightweight path for large playlists: it finds audio,
downloads it as MP3, and writes the playlist, and does nothing else.

**Skipped** compared with the normal pipeline:

- source validation (no candidate is rejected before downloading)
- search breadth: queries run one at a time and stop at the first query that
  yields a usable candidate, instead of running every query and comparing them
- downloaded-audio validation (no duration/bitrate/content checks)
- candidate retry after a failed download beyond the candidates that single
  search already returned
- the download cache (neither consulted nor populated)
- all metadata work: ID3 tags, album cover, artist image, lyrics — and the
  network lookups those need

**Kept**: playlist parsing, local-library matching, source searching, the
download itself (including yt-dlp error classification and the complete-MP3
check), filename sanitisation, M3U generation, progress reporting and the
bounded worker pools.

It is much faster because the expensive per-track work is the network round
trips: 6 parallel searches become 1, one or two validation passes are removed,
and no artwork/lyrics requests happen at all. The trade-off is confidence: a
fast-mode match can be a live version, a cover or a wrong artist, and the MP3s
carry no tags, artwork or lyrics.

A track that finds no usable source fails immediately instead of falling back
into the normal pipeline, so one bad track never slows the rest of a playlist.

Enable it either globally in `config.toml`:

```toml
[fast]
enabled = true
```

or per run with the **Fast mode** toggle on the processing page.

## Candidate Retry

When several plausible candidates exist:

1. Download the highest-ranked candidate.
2. Validate the resulting audio.
3. If invalid, try another plausible candidate.
4. Stop when a valid recording is found or candidates are exhausted.

Do not retry obviously unsuitable candidates.

## yt-dlp

Use yt-dlp for downloading.

Reuse existing downloader code where practical.

The downloader should:

- accept a source URL
- download the source
- extract/convert audio
- produce MP3
- store output in a controlled directory
- report failures
- detect incomplete output

## ffmpeg

Use ffmpeg for audio extraction/conversion where required by the selected yt-dlp format.

## Output Naming

Use filesystem-safe deterministic filenames.

Do not use raw metadata as shell commands.

## Temporary Files

Temporary yt-dlp/ffmpeg files must remain inside controlled job directories.

## Concurrency

Use bounded concurrency.

Avoid spawning an uncontrolled number of yt-dlp or ffmpeg processes.

Track-level download concurrency is a separate, smaller cap
(`download.workers`) than search concurrency (`web.resolve_workers`), so a
large playlist can search broadly without launching an uncontrolled number of
downloads. Per-track yt-dlp/ffmpeg work stays inside the job's bounded worker
pool; yt-dlp's own internal concurrency (fragment downloads) is left to
yt-dlp rather than duplicated.

Each download is additionally bounded by a wall-clock timeout
(`download.timeout`), and search and download socket operations use a network
timeout, so a hung source cannot occupy a worker or the machine indefinitely.
Retries are capped and bounded, so a failing download fails instead of
retrying forever.

## Validation

Successful yt-dlp execution does not automatically mean successful track resolution.

The resulting audio must pass downloaded-audio validation before being included in the final playlist.

Playable cinematic and instrumental files may contain ambience, impacts, or
vocal-like textures. These are warning-level signals unless there is strong
evidence of actual spoken dialogue; clear speech remains rejectable. A
downloaded vocal version of an explicitly instrumental target fails recording
validation and triggers candidate retry.

## Embedded Metadata

What is written into a generated MP3 is configurable, so a library that only
wants audio can keep files as small as possible:

- `[metadata] enabled` (default `true`) — master switch. When `false`, nothing
  is embedded and no lookup or download runs for it: no text tags, album cover,
  artist image or lyrics. It overrides the per-feature options below.
- `[metadata] tags` (default `true`) — standard ID3 text fields (title,
  artists, album, album artist, track/disc number, year, genre, comment). When
  `false` the downloader's own tags are left untouched.
- `[artwork] album_artwork` (default `true`) — embed the album front cover.
  Embedded covers are by far the largest part of the metadata, so this is the
  biggest space saving.
- `[artwork] artist_artwork` (default `true`) — embed the artist profile image
  (see [artwork](artwork.md)).
- `[lyrics] enabled` (default `true`) — embed lyrics (see below).

Disabling an option also skips its network lookups, so it saves time as well as
space. Filenames, M3U generation and track resolution are unaffected: a track
is never marked failed because metadata embedding is off.

Fast mode skips metadata enrichment altogether, which is equivalent to turning
the master switch off for the tracks it downloads.

## Lyrics

After a track resolves, its lyrics are written into the file's standard lyrics
field (ID3 `USLT`) as part of metadata enrichment.

Lyrics are retrieved through the `syncedlyrics` library, which searches public
lyrics providers itself. SpotM3U performs no web search of its own, scrapes no
lyrics sites, and hardcodes no provider URLs or parsers.

**Synced lyrics are preferred.** The library looks for timed lyrics first and
falls back to plain text, so a track gets the best form a provider has. When
the lyrics are timed, the timestamps are kept in the lyrics field *and* an
`.lrc` file is written next to the audio file (`song.mp3` -> `song.lrc`), which
is what players that read a sidecar instead of ID3 frames look for. Plain
lyrics go into the field only — there is nothing to time, so no sidecar is
written. Deezer is not a lyrics source: its public API has no lyrics endpoint,
and its only lyrics route is a private, broken web-player endpoint.

Lyrics are optional enrichment and never affect resolution:

- a track with no lyrics keeps an empty lyrics field
- an unavailable provider or a network failure is logged at debug level and ignored
- a malformed or empty library result is discarded
- an audio file that cannot hold the field is left untouched
- a sidecar that cannot be written only costs the sidecar, not the embedded lyrics
- existing metadata and embedded artwork are preserved
- an `.lrc` from an earlier run is never deleted, and a track that finds no
  lyrics keeps the ones already in it

Retrieval is enabled by `[lyrics] enabled` in `config.toml` (default true) and
is skipped when the `[metadata] enabled` master switch is off. Fast mode skips
metadata enrichment altogether, so it never requests lyrics.

The [result page](web.md) labels each track **Synced lyrics** or **Plain
lyrics** (nothing when the file has no lyrics), read from the file itself, so
the form a download really carries is visible without opening it.

## Cache

Successful downloads may be cached and reused when their association with the requested recording is sufficiently strong.

Cache reuse must not rely solely on filenames.

Deleting downloads by hand is supported. A cached entry is only reused while
its audio file is still on disk (the manifest lives in the download folder
next to it), and the manifest heals itself: entries whose file has been deleted
are dropped on the next lookup or store, so a stale row can never make SpotM3U
skip a re-download. A finished run reports tracks whose file disappeared as
**missing from disk** and offers a retry that downloads them again, handing out
the playlist itself warns first when any entry it references is gone (see
[web](web.md)), and the retry drops the cached artwork of the deleted tracks so
it is re-fetched with the fresh download (see [artwork](artwork.md)).

Concurrent downloaders that target the same output file (for example two
batch playlists that share a song) are single-flighted: the first caller
downloads, and the others wait and reuse that validated file instead of
downloading it again. Reuse only happens when the file passes audio
validation against the waiting recording; otherwise the caller downloads its
own copy.
