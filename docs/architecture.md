# Architecture

## Overview

This project converts Spotify playlists exported through Exportify into local M3U playlists.

The application does not use:

- Spotify browser automation
- Spotify DOM scraping
- Spotify Web API
- Spotify Premium

The user workflow is:

Exportify
→ Exportify ZIP
→ Flask upload
→ Exportify parser
→ playlist selection
→ local audio resolution
→ online source search when necessary
→ source ranking/validation
→ yt-dlp download
→ downloaded audio validation
→ M3U generation
→ M3U download

## Important Existing Component

Task 10 already implements the local audio resolver.

Do not replace it.

The local resolver searches the user's existing audio library for suitable MP3/FLAC/etc. files.

The new online pipeline is a fallback/additional resolution strategy.

## Resolution Architecture

For each Track:

1. Try local audio resolution.
2. If a suitable local file exists:
   - use it
3. Otherwise:
   - search online sources
   - rank candidates
   - validate the selected candidate
   - download using yt-dlp
   - convert to MP3
   - validate the resulting audio
4. Store the resulting local audio path.
5. Use the local path when generating the M3U.

Conceptually:

Track
├── Local Audio Resolver
│ └── existing local audio file
│
└── Online Resolver
├── Source Search
├── Candidate Ranking
├── Source Validation
├── yt-dlp Download
└── Downloaded Audio Validation
└── downloaded MP3

## Core Models

### Track

Represents the Spotify/Exportify track metadata.

Suggested fields:

- title
- artists
- album
- duration_ms
- spotify_id
- spotify_url

### SourceCandidate

Represents an online candidate.

Suggested fields:

- url
- title
- uploader/channel
- duration_s
- source_type
- metadata
- ranking/confidence

### ResolvedTrack

Represents the final local audio associated with a Track.

Suggested fields:

- track
- local_path
- resolution_method
- source_url
- status
- validation information

## Module Boundaries

### Exportify Parser

Responsible only for:

- reading Exportify files
- parsing playlists
- creating Track objects

### Track Normalization

Responsible for:

- normalization
- title/artist comparison utilities
- version-marker handling

### Local Audio Resolver

Responsible for:

- finding existing local audio
- matching Track metadata to local files

This is Task 10 and should remain independent.

### Online Source Search

Responsible for:

- searching for online candidates
- returning candidate metadata

It does not download files.

### Candidate Ranking

Responsible for:

- comparing candidates against Track metadata
- ranking candidates
- exposing confidence

### Source Validation

Responsible for:

- rejecting obviously unsuitable sources
- deciding whether a candidate may proceed to download

### yt-dlp Downloader

Responsible for:

- downloading an accepted source
- extracting/converting audio to MP3
- returning the resulting local path

### Downloaded Audio Validation

Responsible for:

- checking the resulting audio file
- verifying duration/format/readability
- detecting obvious unwanted content where technically possible

### M3U Writer

Responsible only for:

- receiving ordered local audio paths
- writing the M3U

It must not perform matching or downloading.

## Key Principle

Source selection and audio validation are separate.

A source can have perfect-looking metadata and still contain unwanted dialogue or sound effects.

Therefore:

metadata match
≠
guaranteed clean recording

## Output

The final M3U references the actual local MP3 files produced by:

- existing local library resolution, or
- successful yt-dlp downloads.

The M3U itself does not download anything.
