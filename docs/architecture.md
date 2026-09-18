# Architecture

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
→ M3U generation
→ M3U download

## Resolution Strategy

For every Track:

1. Try the existing local audio resolver.
2. If a suitable local file exists, use it.
3. Otherwise search online sources.
4. Rank plausible candidates.
5. Reject only candidates with strong evidence of being unsuitable.
6. Download a plausible candidate with yt-dlp.
7. Validate the downloaded audio.
8. If validation fails, try another plausible candidate when available.
9. Store the successful local file.
10. Pass the resolved file to the M3U writer.

## Important Matching Principle

The online resolver must not require perfect metadata.

The pipeline is intentionally:

**permissive candidate discovery**
→ **download**
→ **actual audio validation**
→ **final acceptance**

This prevents harmless metadata differences from causing large numbers of false failures.

## Local Audio Resolver

Task 10 is the existing local resolver.

It searches existing MP3/FLAC/etc. files and attempts to match them to Tracks.

It remains a separate resolution strategy.

Do not duplicate its functionality inside the online resolver.

## Online Source Search

Searches for candidate recordings using Track metadata.

Search queries are a small set of focused artist-aware queries (for example
`{artist} {title}`, `{artist} {title} lyrics`, `{artist} {title} official
audio`, `{title} {artist}`). Search queries always include the requested
artist alongside the song title, so common titles such as `演员` still surface
the correct artist's official upload instead of only other-artist covers.

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
audio/lyrics source for the same recording.

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

- existing local files from Task 10
- successfully downloaded MP3 files
