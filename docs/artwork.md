# Album Artwork / Cover Art Resolution

## Lookup priority

Cover art belongs to a **release**, so artwork is resolved album-first:

1. **Artist + album / release** — the primary lookup, and an **authoritative
   verified cover** whenever a reliable album value exists (quantified by
   `_has_reliable_album`). MusicBrainz / Cover Art Archive / iTunes results are
   only accepted when their artist + album identity strongly matches the
   target.
2. **Local embedded/sidecar art** — used **only as a fallback** when the album
   lookup is impossible (no reliable album value) or fails (e.g. offline).
   A local file's existing cover is frequently the wrong artwork for the
   target release (mis-tagged file, compilation, previously-downloaded YouTube
   thumbnail), so it is never trusted ahead of a verified release cover.
3. **Artist + song title** — used when album metadata is missing, empty, or
   clearly unreliable, or when the album and local lookups produce no result.
4. **No artwork** when nothing is reliable.

Song-title search is never the primary method while a reliable album value
exists, because it can return alternate releases, covers, singles, live
versions and remixes.

The release-first behavior can be relaxed for speed through the optional
`[artwork] verify_local` setting in `config.toml` (default `true`). When set
to `false`, existing local art is trusted and used immediately, so files that
already carry a cover perform no artwork network lookups at all — at the cost
of possibly borrowing a wrong local cover.

### Reading a local file's embedded art

SpotM3U uses only an unambiguous front cover from a local file: an explicit
type-3 (front cover) APIC frame, or the sole APIC frame in the file. When
several frames exist and none is marked as a front cover (a back cover or
artist photo could be picked), the file is treated as having no cover rather
than guessing. When multiple front covers exist the largest one wins.

## External artwork sources

SpotM3U queries established metadata services only; it does not scrape
arbitrary websites:

- **MusicBrainz** (`musicbrainz.org/ws/2`, release-group search) — finds the
  release group for artist + album.
- **Cover Art Archive** (`coverartarchive.org`) — returns front-cover images
  for a release group.
- **Apple iTunes Search API** (`itunes.apple.com/search`) — album artwork from
  the public iTunes catalog, used to supplement MusicBrainz and for the
  song-title fallback.
- **Deezer** (`api.deezer.com`) — artist profile images only (see *Artist
  artwork* below). Deezer is never used to resolve album covers.

Candidates are only accepted when their **artist identity strongly matches**,
even for common album/song names (e.g. the many songs named "Home"), and album
identity must match for album lookups. Low-confidence or mismatched results are
rejected rather than used.

## Artist artwork

Alongside the album cover, the **performing artist's profile image** is
embedded into each generated MP3 as an ID3v2 `APIC` frame of type **8**
(artist/performer), next to the front-cover frame (type 3). Both frames coexist:
embedding artist artwork replaces only frames of the same picture type, so the
album cover is never overwritten (and vice versa). ID3 metadata written for the
track is untouched, and players that take the first image as the cover keep
showing the album cover.

Artist images are **not** available from the release-oriented sources: Spotify
itself is off limits (no Web API, no scraping), and MusicBrainz, Cover Art
Archive and iTunes return no artist image. The artist identity from the export
is therefore resolved through **MusicBrainz** and **Deezer's public catalog**,
which serves profile images (JPEG) without authentication. Deezer's JPEG images
are embedded as returned; a format that ID3 does not support natively is detected
by its magic bytes and only then would need conversion.

### Identity is required, not guessed

**An artist image is embedded only when the identity is established by evidence.**
A wrong face is permanent — it is written into every MP3 by that artist — so a
missing profile image is the better outcome. Fan counts are **never** evidence:
a famous namesake is still a namesake. Evidence is looked for in this order:

1. **MusicBrainz artist identity linked to a Deezer artist page.** The track's
   release (or, without an album, its recording) gives the artist's MBID, and
   MusicBrainz's editor-maintained URL relationships (`inc=url-rels`) give the
   artist's Deezer page. That is an exact external identity, so the image is
   fetched by id and **no name search happens at all**. When MusicBrainz links
   several Deezer pages for one artist (a canonical page plus a duplicate stub),
   Deezer's own ranking picks between *the artist's own pages* — the identity is
   already established by the relationship, so this only chooses the real page.
2. **MusicBrainz artist identity plus a single exact-name Deezer artist.** When
   MusicBrainz knows the artist but records no Deezer page, one exact-name
   candidate is identified rather than chosen.
3. **Matching release or track evidence in the candidate's own Deezer releases**
   (`api.deezer.com/artist/<id>/albums`), unique among the exact-name candidates.
   Deezer lists singles there too, so an album-less track is still confirmed when
   a single carries its track title. When the album and the track point at
   different candidates, nothing is embedded.
4. **A sole exact-name Deezer candidate that MusicBrainz also knows by exactly
   that name.** A unique name is still only a name, so it needs the second
   catalog to agree before it counts.

Everything weaker is a *likely* or *unknown* candidate and returns no artist
artwork: several exact-name artists with no corroborating release, a release list
that cannot be read, a name that only partly matches. Only a name that matches
the requested one **exactly** once normalized is even considered — Deezer's
ranking puts `Adèle & Zalem`, `Adele & Andy` and `Mortelle Adèle` ahead of the
requested `Adele`. Popularity only orders which candidates are worth checking, so
the common case does not spend its requests on an obscure namesake; at most three
candidates have their releases checked. Rejections and successful resolutions
(artist id, confidence, and the evidence behind it) are logged at debug level.

Artist images are cached under `<download_dir>/artwork_cache/artists/`, keyed by
normalized artist alone (a separate directory from the release cache, so artist
and release identities can never collide or be mistaken for each other). Every
track credited to the same artist reuses one download, and concurrent workers
share a single in-flight fetch through the same deduplication used for album
artwork.

Artist artwork is **optional enrichment**: a missing, invalid or unavailable
image is recorded as a non-fatal metadata error (`artist artwork not found`,
`artist artwork embed failed`), the album artwork and ID3 fields are unaffected,
and track generation never fails because of it. It is enabled by default and can
be turned off with the `[artwork] artist_artwork` setting in `config.toml`.

## Embedding options

Both artwork frames are optional and can be turned off in `config.toml`:

- `[artwork] album_artwork` (default `true`) — when `false`, no album cover is
  looked up, downloaded or embedded. Embedded covers dominate the size of a
  generated MP3, so this is the biggest space saving.
- `[artwork] artist_artwork` (default `true`) — the artist profile image.

Disabling either option skips its network lookups as well as its write, and the
`[metadata] enabled` master switch skips both (see
[downloads](downloads.md#embedded-metadata)).

## Multiple artists

Tracks can carry several credited artists. Album artwork identity uses the album
artist when present, otherwise the first structured artist. Artist names are
**never joined** into a malformed lookup string such as `Artist1Artist2`.
ID3 metadata still records every collaborating artist.

## Caching

- Positive results are cached on disk under `<download_dir>/artwork_cache/`,
  keyed by normalized artist + album (or artist + song title for the fallback).
- The same directory is shared across runs and jobs, so tracks from the same
  release reuse one lookup.
- Each cached image records its **source** in a sibling `*.src` marker file.
  Entries from a verified release lookup (`coverartarchive`, `itunes`,
  `itunes-song`) are authoritative and are served straight from cache. Entries
  from local embedded/sidecar art (`embedded:*`, `sidecar:*`), and legacy
  entries written before source tracking existed, may carry a wrong cover and
  are **re-verified** against the release on a later networked run; offline they
  remain the best-available image.
- Artist images record the **Deezer artist id** they were taken from *and what
  proved the identity*, as `deezer-artist:<id>:verified:<evidence>` (for example
  `deezer-artist:75798:verified:mb-artist-url`), so an embedded image can be
  traced back to the artist entity it belongs to and to the evidence that
  authorized it. A marker that names an id but no verification was written when
  name and popularity were enough, and may hold any namesake's image, so it is
  **re-resolved** rather than served: an install that cached the wrong artist
  image replaces it on the next run without clearing the cache by hand. Entries
  written before ids were recorded at all are treated the same way.
- A bounded in-process memo plus per-identity **in-flight deduplication**
  ensures concurrent workers resolving tracks from the same album share a
  single external fetch instead of repeating it.
- **Negative results are never persisted.** A later run may have network access
  or a newly indexed release, so a previous failure does not block a retry.
- **Deleted downloads forget their artwork.** An entry is keyed by release
  identity rather than by audio file, so a deleted download never makes a cached
  cover *wrong* - it only leaves an image nothing in the folder references. A
  retry that re-downloads a deleted track drops that track's release entry (the
  image, its `*.src` marker and the in-process memo) so the fresh download gets
  fresh artwork, and reclaims the disk the entry held. Artist profile images are
  shared by every album of that artist, so they are dropped only when no other
  track still needs them. Nothing is served from a pruned entry, so this is
  housekeeping rather than correctness: whatever is pruned is simply fetched
  again next time it is needed.

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
(`GET /api/jobs/<job_id>/playlists/<playlist_id>/artwork/<index>`); the client does not
block on, or depend on, artwork being available.

A track whose audio file was deleted by hand shows the placeholder, not its
cached cover: the row would otherwise display artwork for a download that is no
longer there, and the artwork route answers `404` for that track until the retry
downloads it again.

## Failure behavior

Artwork resolution failures are always non-fatal. A track is never marked
failed or ambiguous merely because its artwork could not be resolved or
embedded; missing album/artist artwork is reported on the metadata result (see
`MetadataResult.artwork_embedded` / `artist_artwork_embedded` and `errors`) and
the track keeps its resolved audio file.