# Architecture

For visual references to the user-facing workflow, see the
[screenshot plan](screenshots.md). The planned result-page capture should
illustrate the final resolved/unresolved boundary described below; it must not
be treated as an architecture diagram or as evidence for unsupported behavior.

## Overview

This project converts Spotify playlists exported through Exportify into local M3U playlists.

The application does not use:

- Spotify browser automation
- Spotify DOM scraping
- Spotify Web API
- Spotify Premium

Workflow:

Exportify
→ ZIP upload
→ Exportify parser
→ playlist selection
→ local audio resolver
→ online source search when necessary
→ candidate ranking
→ source validation
→ yt-dlp download
→ downloaded-audio validation
→ metadata/artwork enrichment
→ final verified MP3
→ M3U generation
→ M3U download

## Resolution Strategy

For every Track:

1. Try the existing local audio resolver.
2. If a suitable local file exists, use it.
3. Otherwise search online sources.
4. Rank plausible candidates.
5. Validate recording/version identity, including instrumental versus vocal.
6. Reject only candidates with strong evidence of being unsuitable.
7. Download a plausible candidate with yt-dlp.
8. Validate the downloaded audio.
9. If validation fails, try another plausible candidate when available.
10. Store the successful local file.
11. Enrich the resolved MP3 with album artwork, artist artwork, lyrics and
    standard ID3 metadata when possible.
12. Keep the track in a resolved state even if artwork or lyrics are unavailable.
13. Pass the resolved file to the M3U writer.

## Important Matching Principle

The online resolver must not require perfect metadata.

The pipeline is intentionally:

**permissive candidate discovery**
→ **download**
→ **actual audio validation**
→ **final acceptance**

This prevents harmless metadata differences from causing large numbers of false failures.

## Local Audio Resolver

The local resolver searches existing MP3/FLAC/etc. files and attempts to match them to Tracks.

It remains a separate resolution strategy.

Do not duplicate its functionality inside the online resolver.

## Online Source Search

Searches for candidate recordings using Track metadata.

Search queries are a small set of focused artist-aware queries (for example
`{artist} {title}`, `{artist} {title} official audio`, `{title} {artist}`).
Search queries always include the requested artist alongside the song title, so
common titles such as `演员` still surface the correct artist's official upload
instead of only other-artist covers.

Lyric-video uploads are no longer searched for: lyrics metadata comes from a
dedicated lyrics library (see "Lyrics Enrichment" below), and a lyric video is
a low-signal audio source. Lyric-titled uploads that still surface from the
queries above remain recognized and ranked below pure audio sources.

The searcher runs **every** query and aggregates all candidates (deduplicated
by URL) before any ranking or download, so a candidate that appears only in a
later query is still discovered. It returns candidates but does not decide
correctness and never downloads.

## Candidate Ranking

Ranking separately evaluates **recording identity** (is this the requested
song by the requested artist?) and **source quality** (is this upload a good
way to extract clean song audio?).

Identity signal components:

- **artist identity** (first-class, ordering-dominant)
- title
- duration
- version
- uploader/channel and description/tags (supporting identity evidence)

For common titles (e.g. `演员`), a confirmed artist identity outranks an exact
title match, and a conflicting explicit artist rejects the candidate even when
the title matches exactly. `Official MV`/`Official Audio` labels are separated
from core song identity so official artist uploads still match.

Source-quality preference (only among already-correct recordings):

1. official audio / audio upload
2. lyric video / lyrics
3. clean official song upload
4. official music video
5. other legitimate music upload
6. live / performance (only when requested)
7. covers / remixes (only when explicitly requested)

An official music video is a valid fallback but never outranks an
audio/lyrics source for the same recording. Lyric videos are not searched for,
but when one surfaces from another query it still outranks a music video and
loses to a pure audio upload.

Each candidate decision is logged with its score, the identity-evidence
breakdown, and the source-quality tier.

Missing metadata should generally reduce available evidence rather than count
as proof of mismatch.

## Source Validation

Rejects candidates when there is strong evidence that they are unsuitable.

Examples:

- wrong song
- wrong artist (a conflicting explicit artist outweighs an exact title match)
- explicit cover by another artist (`cover`, `翻唱`)
- karaoke
- obvious remix
- live recording conflicting with requested version
- movie scene
- trailer
- interview
- reaction

It should not reject candidates simply because metadata is incomplete.

## yt-dlp Downloader

Downloads accepted/plausible online candidates and converts them to MP3.

The downloader is independent of source ranking.

## Downloaded Audio Validation

Validates the actual resulting audio.

This may inspect:

- duration
- format
- readability
- silence
- obvious speech
- obvious interruptions
- other detectable non-music content

The system must acknowledge that automatic "music only" detection is imperfect.

## Candidate Retry

If a plausible candidate fails after download validation, another plausible candidate may be attempted.

Therefore source resolution can look like:

Candidate A
→ download
→ invalid

Candidate B
→ download
→ valid

## Fast Mode

Fast mode is a separate, lightweight resolution path, not a set of switches
threaded through the normal one. `src/spotm3u/fast.py` provides:

- `FastSourceSearcher` — runs the configured queries one at a time and stops
  at the first query that yields an accepted candidate.
- `FastTrackResolver` — subclasses `TrackResolver` to reuse the shared
  resolution helpers and replaces only the searching and downloading phases:
  local match, one search series, one download per candidate already returned,
  no source validation, no downloaded-audio validation, no download cache and
  no metadata enrichment.

The expensive post-download steps live in the shared downloader behind its
`verify` flag, so fast mode downloads through the same yt-dlp options, error
classification and single-flight/locking code as normal mode.

Selecting it is explicit: `ProcessingJob.fast_mode` is set from the `[fast]
enabled` configuration or the per-run toggle on the processing page, and the
resolver factory in `web_jobs.py` builds either `FastTrackResolver` (with
`verify=False` downloads) or the normal `TrackResolver`. Normal mode's
behaviour is unchanged.

## Lyrics Enrichment

Lyrics retrieval is isolated in `src/spotm3u/lyrics.py` and delegates the
search to the `syncedlyrics` library, which queries public lyrics providers
itself. SpotM3U never searches the web for lyrics, scrapes lyrics sites, or
hardcodes provider URLs and parsers.

Plain lyrics found for a track are written into the standard lyrics field
(ID3 `USLT`) alongside the existing tags, preserving all other metadata and
embedded artwork. Retrieval is optional enrichment: a missing match, a provider
or network failure, an unusable result, or an audio format that cannot hold the
field leaves the track resolved and the lyrics field empty. Failures are logged
at debug level so normal downloads stay quiet.

`[lyrics] enabled` in `config.toml` (default true) turns retrieval off. Fast
mode skips metadata enrichment entirely, so it never requests lyrics.

## Metadata Embedding Options

Embedding is controllable so audio-only libraries can skip it: `[metadata]
enabled` is a master switch that writes nothing at all, `[metadata] tags`
skips the standard ID3 text fields, `[artwork] album_artwork` skips the cover,
`[artwork] artist_artwork` skips the artist image, and `[lyrics] enabled` skips
lyrics. A disabled option also skips its network lookups. None of them affect
filenames, M3U generation, or whether a track resolves.

## M3U Writer

Receives the final ordered local audio paths.

It does not perform:
- searching
- matching
- downloading
- validation

## Final Output

The generated M3U references local audio files.

Those files may be:

- existing local files
- successfully downloaded MP3 files

## Diagnostics

Every per-track processing record is logged with a job and track identifier so
a failed track can be traced through its stages (local resolution, search,
source validation, download, audio validation) up to its final failure reason.
Structured context is provided by `src/spotm3u/log.py`; the `spotm3u` package
logger is configured through `[web] log_level` in `config.toml` or the
`SPOTM3U_LOG_LEVEL` environment variable (set to `DEBUG` for full stage
tracing). Credentials, authentication secrets, and private data are never
logged.
