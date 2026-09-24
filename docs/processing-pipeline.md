# Processing pipeline profile

Task 95 baseline for the later concurrency work. This note describes the
current architecture and the timing records emitted by a normal conversion.

## Current flow

```text
Exportify ZIP
  -> parse tracks
  -> for each track:
       local match
       -> online search
       -> rank candidates
       -> validate source
       -> download with yt-dlp/ffmpeg
       -> validate audio
       -> write ID3 fields
       -> lookup/embed album artwork
       -> lookup/embed artist artwork
       -> lookup/embed lyrics
  -> write ordered M3U
```

With one worker this flow is sequential. With multiple workers, `ProcessingJob`
first runs local matching and online search for all tracks, then runs candidate
validation, download, audio validation, and metadata enrichment in the bounded
download pool. Results are stored by playlist index, so completion order never
changes M3U order.

Track parsing and M3U generation happen once per playlist. Matching, searching,
source validation, downloading, audio validation, ID3 writing, lyrics lookup,
and artwork lookup happen once per resolved track. Artwork caches and the
in-flight artwork registry deduplicate repeated album or artist identities, but
metadata embedding remains per audio file.

## Dependencies and bottlenecks

Audio download depends on source search, candidate ranking, and source
validation. Audio validation depends on the downloaded file. ID3, lyrics, and
artwork enrichment depend on a valid audio file but do not affect the audio
download itself; they currently delay a track's final completion because they
run inline after validation. Album and artist artwork lookups, and lyrics
lookups, are network-bound. yt-dlp/ffmpeg is network, CPU, and disk-bound;
audio validation and ID3 embedding are primarily disk/CPU-bound. M3U writing is
small, ordered, and playlist-scoped.

Safe independent work after audio validation includes lyrics, album artwork,
artist artwork, and the separate ID3 writes, provided writes to the same audio
file are serialized. Candidate attempts must remain ordered by ranking, and
M3U output must remain in playlist order.

## Timing diagnostics

The pipeline logs structured records using `timing stage=<name>
duration_ms=<value>`. Current stages are `matching`, `searching`,
`audio-download`, `metadata-embedding`, `album-artwork`, `artist-artwork`,
`lyrics`, and `job` completion duration. Records include the existing job and
track context where available. Set `SPOTM3U_LOG_LEVEL=INFO` or `DEBUG` and
capture the log for a run; aggregate by stage to compare scenarios without a
debugger.

## Repeatable benchmark procedure

Use representative Exportify playlists containing approximately 50, 250, and
1,000 tracks. Keep the source data, configuration, network, worker counts, and
cache state fixed for each comparison. Run each size once with many unique
artists, repeated artists/albums, missing lyrics, and missing artwork where
available; repeat with a warm cache to separate lookup cost from download cost.

Record wall-clock job duration, successful/failed counts, average track
duration, the sum and average of each timing stage, cache hits, external
request counts, and retries. The current repository does not contain a
deterministic 50/250/1,000-track fixture or an offline provider harness, so
real-network benchmark numbers must be captured from the operator's chosen
representative playlists rather than fabricated here. The timing records above
provide the baseline data for Tasks 96-100.
