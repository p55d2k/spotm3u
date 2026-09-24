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

`spotm3u.metadata_jobs.MetadataJob` is the explicit handoff between these
pipelines. It contains the track and metadata destination before audio exists;
`with_audio_path()` creates the ready-to-run job after download. Its `run()`
method delegates to the existing enrichment implementation and returns the
existing `MetadataResult`, preserving provider behavior and cache semantics.
This is a queueable boundary for Task 97 without starting additional workers
or changing request rates in this task.

Live job snapshots expose three separate milestones: `searched` counts tracks
whose local/online search phase has finished, `resolved` counts tracks whose
audio resolution (local match, download, or terminal failure) has finished, and
`completed` counts tracks whose metadata enrichment and finalization have also
finished. The processing UI displays all three as equal thirds of the progress
bar, so slow downloads or metadata work cannot look like an idle job; download
and metadata progress can advance concurrently in their own thirds.

Task 97 now uses a bounded metadata pool. Normal web jobs defer enrichment
from the resolver, submit completed audio files to up to
`metadata.workers` workers (default `3`, capped at `8`), and finalize each
track only after its metadata job returns. Download workers continue submitting
audio independently while metadata jobs run; metadata failures are logged and
do not change a successful audio resolution. Fast mode remains metadata-free.

Provider requests use shared throttlers rather than worker-local sleeps.
MusicBrainz is limited to one request per second, while Cover Art Archive,
iTunes, Deezer, and lyrics use separate bounded limiters. The limiter handles
transient network/5xx failures and 429 responses with `Retry-After`-aware
exponential backoff, and keeps request, retry, failure, rate-limit, and
latency counters for diagnostics. Cached artwork paths do not enter the
request layer.

Finalization is driven by individual metadata futures: a completed track is
committed to job state as soon as its own audio and metadata are ready rather
than waiting for the playlist's slowest track. Progress exposes the
`enriching-metadata` stage before completion, and finalization timing is logged
as `timing stage=finalization`. Worker pools remain bounded and shut down before
the job completes.

Source validation classifies strong contradictions (wrong identity, explicit
cover/karaoke/spoken content, or extreme duration mismatch) as hard rejections.
Missing metadata, source-type preference, and moderate duration differences
remain warnings or ranking penalties; they do not reject a plausible recording.
This preserves retry behavior while avoiding false negatives from incomplete
provider metadata.

Metadata cache keys prefer stable track IDs for lyrics and normalized
artist/album identities for artwork. Successful results are cached and
concurrent misses use single-flight coordination, so duplicate tracks or
artists share one request. Missing or failed results are not retained as
permanent negative entries; they can recover on a later run. Cache metrics
include requests, hits, misses, and single-flight joins.

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
