# Downloads

## Purpose

The project downloads online sources for tracks that cannot be resolved from the existing local audio library.

Downloaded audio becomes local MP3 files that can be referenced by the generated M3U.

## Download Directory

Downloads and the generated M3U are written to `DOWNLOAD_DIR`, which defaults
to `<MUSIC_LIBRARY>/SpotM3U-downloads/`.

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
lyrics sites, and hardcodes no provider URLs or parsers. The library is asked
for plain (unsynchronised) lyrics, which is what the standard lyrics field
holds.

Lyrics are optional enrichment and never affect resolution:

- a track with no lyrics keeps an empty lyrics field
- an unavailable provider or a network failure is logged at debug level and ignored
- a malformed or empty library result is discarded
- an audio file that cannot hold the field is left untouched
- existing metadata and embedded artwork are preserved

Retrieval is enabled by `[lyrics] enabled` in `config.toml` (default true) and
is skipped when the `[metadata] enabled` master switch is off. Fast mode skips
metadata enrichment altogether, so it never requests lyrics.

## Cache

Successful downloads may be cached and reused when their association with the requested recording is sufficiently strong.

Cache reuse must not rely solely on filenames.

Concurrent downloaders that target the same output file (for example two
batch playlists that share a song) are single-flighted: the first caller
downloads, and the others wait and reuse that validated file instead of
downloading it again. Reuse only happens when the file passes audio
validation against the waiting recording; otherwise the caller downloads its
own copy.
