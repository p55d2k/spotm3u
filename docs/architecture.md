# Architecture

SpotM3U converts Spotify playlists exported through
[Exportify](https://exportify.net/) into local M3U playlists. It does not use
Spotify browser automation, DOM scraping, the Spotify Web API, or Spotify
Premium.

```text
Exportify
 → ZIP upload
 → Exportify parser
 → playlist selection
 → local audio resolver
 → online source search when no local match
 → candidate ranking
 → source validation
 → yt-dlp download
 → downloaded-audio validation
 → metadata / artwork enrichment
 → final verified MP3
 → M3U generation
 → M3U download
```

## Resolution strategy

For every track:

1. Try the local audio resolver first.
2. If a suitable local file exists, use it.
3. Otherwise search online sources.
4. Rank the plausible candidates.
5. Validate recording and version identity, including instrumental versus vocal.
6. Reject only candidates with strong evidence of being unsuitable.
7. Download the plausible candidate with `yt-dlp`.
8. Validate the downloaded audio.
9. If validation fails, try another plausible candidate when one exists.
10. Store the successful local file.
11. Enrich the resolved MP3 with album artwork, artist artwork, lyrics, and
    standard ID3 metadata where possible.
12. Keep the track resolved even if artwork or lyrics are unavailable.
13. Pass the resolved file to the M3U writer.

The online resolver is intentionally *permissive during discovery, conservative
during validation*: it does not require perfect metadata. The pipeline is
designed as permission → download → actual-audio validation → final acceptance
so harmless metadata differences do not cause a flood of false failures. The
local resolver stays a separate strategy and its functionality is not
duplicated inside the online resolver.

## Online source search

Search uses a small set of focused, artist-aware queries (for example
`{artist} {title}`, `{artist} {title} official audio`, `{title} {artist}`). The
requested artist is always included alongside the song title, so common titles
such as `演员` still surface the correct artist's official upload instead of
only other-artist covers.

Lyric-video uploads are not searched for deliberately: lyrics metadata comes
from a dedicated lyrics provider (see "Lyrics" below), and a lyric video is a
low-signal audio source. Lyric-titled uploads that still surface from the
queries above are recognized and ranked below pure audio sources.

Every query runs and all candidates are aggregated and de-duplicated by URL
before any ranking or download, so a candidate that appears only in a later
query is still discovered. The searcher returns candidates but does not decide
correctness and never downloads.

## Ranking

Ranking separately evaluates **recording identity** (is this the requested
song by the requested artist?) and **source quality** (is this upload a good
way to extract clean song audio?).

Identity signals include:

- artist identity — first-class, ordering-dominant
- title
- duration
- version
- uploader/channel and description/tags as supporting identity evidence

For common titles (e.g. `演员`), a confirmed artist identity outranks an exact
title match, and a conflicting explicit artist rejects the candidate even on an
exact title match. Official MV/audio labels are separated from core song
identity so official artist uploads still match.

Among already-correct recordings, source-quality preference is:

1. official audio / audio upload
2. lyric video / lyrics
3. clean official song upload
4. official music video
5. other legitimate music upload
6. live / performance (only when requested)
7. covers / remixes (only when explicitly requested)

Each decision is logged with its score, the identity-evidence breakdown, and the
source-quality tier. Missing metadata reduces available evidence rather than
counting as proof of mismatch.

## Source validation

Candidates are rejected only on strong evidence of being unsuitable: wrong
song, wrong artist (a conflicting explicit artist outweighs an exact title
match), an explicit cover by another artist (`cover`, `翻唱`), karaoke, an
obvious remix, a conflicting live version, or non-music content such as movie
scenes, trailers, interviews, and reactions. Incomplete metadata alone never
rejects a candidate.

## Download and audio validation

Accepted candidates are downloaded with `yt-dlp` and converted to MP3. The
downloader is independent of ranking. The actual resulting audio is then
validated: duration, format, readability, silence, obvious speech,
interruptions, and other detectable non-music content. SpotM3U is deliberately
honest that automatic "music-only" detection is imperfect.

## Fast mode

Fast mode is a separate, lightweight resolution path rather than a set of
switches threaded through the normal one. `FastSourceSearcher` runs the
configured queries one at a time and stops at the first query that yields an
accepted candidate, and `FastTrackResolver` reuses the shared resolution
helpers, replacing only the search and download phases: local match, one search
series, one download per candidate, and none of the expensive post-download
steps — source validation, downloaded-audio validation, download cache, or
metadata enrichment. Fast mode downloads through the same `yt-dlp` options,
error classification, and single-flight/locking as normal mode. Normal mode's
behavior is unchanged.

## Lyrics

Lyrics retrieval lives in `src/spotm3u/lyrics.py` and delegates the search to
the `syncedlyrics` library, which queries public lyrics providers itself.
SpotM3U never searches the web for lyrics, scrapes lyrics sites, or hardcodes
provider URLs and parsers. See [downloads](downloads.md#lyrics) for how the
lyrics are parsed and embedded.

## Metadata and artwork embedding

Embedding is controllable so audio-only libraries can skip it: `[metadata]
enabled` is a master switch that writes nothing at all, `[metadata] tags`
writes the standard ID3 text fields, and `[artwork] album_artwork`,
`[artwork] artist_artwork`, and `[lyrics] enabled` each control their feature.
A disabled option also skips its network lookups. None of these affect
filenames, M3U generation, or whether a track resolves.

## M3U generation

The M3U writer receives the final ordered local audio paths and writes the
playlist. It does not search, match, download, or validate — see
[M3U playlists](m3u.md).

## Diagnostics

Every per-track processing record carries a job and track identifier, so a
failed track can be traced through its stages — local resolution, search,
source validation, download, audio validation — up to its final failure reason.
Structured context comes from `src/spotm3u/log.py`; the `spotm3u` package
logger is configured through `[web] log_level` in `config.toml` or the
`SPOTM3U_LOG_LEVEL` environment variable (set to `DEBUG` for full stage
tracing). Credentials, authentication secrets, and private data are never
logged.