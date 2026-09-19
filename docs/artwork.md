# Album Artwork / Cover Art Resolution

## Lookup priority

Cover art belongs to a **release**, so artwork is resolved album-first:

1. **Artist + album / release** — the primary lookup.
2. **Artist + song title** — used when album metadata is missing, empty, or
   clearly unreliable, or when the album lookup produces no sufficiently
   reliable result.
3. **No artwork** when neither lookup yields a reliable result.

Song-title search is never the primary method while a reliable album value
exists, because it can return alternate releases, covers, singles, live
versions and remixes.

## External artwork sources

spotm3u queries established metadata services only; it does not scrape
arbitrary websites:

- **MusicBrainz** (`musicbrainz.org/ws/2`, release-group search) — finds the
  release group for artist + album.
- **Cover Art Archive** (`coverartarchive.org`) — returns front-cover images
  for a release group.
- **Apple iTunes Search API** (`itunes.apple.com/search`) — album artwork from
  the public iTunes catalog, used to supplement MusicBrainz and for the
  song-title fallback.

Candidates are only accepted when their **artist identity strongly matches**,
even for common album/song names (e.g. the many songs named "Home"), and album
identity must match for album lookups. Low-confidence or mismatched results are
rejected rather than used.

## Multiple artists

The structured multi-artist model (Task 39) is preserved. Artwork identity uses
the album artist when present, otherwise the first structured artist. Artist
names are **never joined** into a malformed lookup string such as
`Artist1Artist2`. ID3 metadata still records every collaborating artist.

## Caching

- Positive results are cached on disk under `<download_dir>/artwork_cache/`,
  keyed by normalized artist + album (or artist + song title for the fallback).
- The same directory is shared across runs and jobs, so tracks from the same
  release reuse one lookup.
- A bounded in-process memo plus per-identity **in-flight deduplication**
  ensures concurrent workers resolving tracks from the same album share a
  single external fetch instead of repeating it.
- **Negative results are never persisted.** A later run may have network access
  or a newly indexed release, so a previous failure does not block a retry.

## Concurrency and rate limits

- External artwork requests are bounded: at most two artwork HTTP requests are
  open at once regardless of resolution worker count, and identical identities
  are fetched once.
- Requests carry a 15 s timeout and a descriptive `User-Agent`.
- MusicBrainz, Cover Art Archive and iTunes operate best-effort; failures are
  logged at debug level and never affect track resolution.

## UI

Artwork thumbnails appear in completed-track rows on the result page and in
live processing rows for completed tracks. The `<img>` element has a consistent
fixed size, aspect ratio and border radius in both light and dark themes; when
artwork is missing or fails to load, a clean placeholder is shown instead of a
broken image. Artwork is served locally from the artwork cache
(`GET /processing/<job_id>/<playlist_id>/artwork/<index>`); the pages do not
block on, or depend on, artwork being available.

## Failure behavior

Artwork resolution failures are always non-fatal. A track is never marked
failed or ambiguous merely because its artwork could not be resolved or
embedded; missing album/artist artwork is reported on the metadata result and
the track keeps its resolved audio file.