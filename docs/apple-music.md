# Apple Music Catalog IDs (experimental)

Apple Music does not render the `SYLT` frame embedded in an imported local MP3,
so a downloaded track shows no synchronized lyrics even though SpotM3U wrote
correct `USLT` + `SYLT` data (see [downloads](downloads.md#lyrics)). Community
experimentation reports that writing **Apple's own catalog track id** into a
local file (the `ITUNESCATALOGID` metadata field) alongside sufficiently
matching metadata may make Music associate the import with its catalog track,
after which Apple's server-side synced lyrics can appear.

That mechanism is **undocumented and unverified**, so it is an experiment, not a
feature: it is **off by default**, it never affects a download, and SpotM3U
never scrapes Apple's lyric service, injects Apple's lyrics, removes `SYLT`, or
depends on a private API. This page records what was built, what was measured,
and what could not be verified.

## Enabling it

```toml
[apple_music]
catalog_id = true
```

When enabled, metadata enrichment additionally resolves the track in Apple's
public catalog and writes `TXXX:ITUNESCATALOGID` with that catalog track id.
Everything else is unchanged: the standard text tags, embedded artwork, and
`USLT`/`SYLT` frames are written exactly as usual, and the ordinary metadata is
what makes the file *look* like its catalog counterpart. Nothing runs when the
option is off, when fast mode skips metadata enrichment, or when
`[metadata] enabled` / `[metadata] tags` is off. Lookups go through the same
rate-limited provider path as the other sources, and only a positive match is
cached (a miss is retried next time, never remembered as a failure).

## How the id is represented

ID3 has no dedicated catalog-id field, so the id goes into a user-defined text
frame: `TXXX` with the description `ITUNESCATALOGID` and Apple's track id as a
plain decimal string (written as ID3v2.3, like the other frames). The MP4/M4A
equivalent (`----:com.apple.iTunes:ITUNESCATALOGID`) does not apply here because
SpotM3U generates MP3. The frame is written by its own minimal helper that only
touches that one field, so existing tags, artwork and lyrics are preserved. The
frame *name* and *string* form are the community-reported ones — see below for
why they could not be confirmed against Music itself.

## Matching rules

A local track is associated with a catalog id **only on strong, multi-field
evidence**. A candidate result must agree on all of:

| Field | Rule |
| --- | --- |
| Title | normalized base title (before the first version marker, remaster/"official audio" noise stripped) is equal |
| Version | identical set of recording-version markers (`live`, `acoustic`, `remix`, `demo`, …); `remastered` is treated as the same recording |
| Artist | normalized identity equality with the album artist (or the first artist) |
| Album | when the local track carries an album, the candidate must come from that album |
| Duration | within 5 seconds or 3%, whichever is larger (catalog rounding) |
| Explicitness / track number / year / region | used as ranking evidence, not as a positive signal |

Title + artist alone is **never** enough. Exactly one candidate must survive;
two indistinguishable candidates (a duplicate album entry, a regional reissue)
are treated as no match rather than guessed between.

## Prototype and findings

Because the Apple Music side cannot be exercised automatically, the prototype is
split in two: an **offline matcher prototype** (`tests/test_apple_music.py`, 13
tests, mocked iTunes responses, no network) and a **manual import check** that a
maintainer with macOS and Apple Music has to run.

Offline prototype results — representative tracks, one row per behavior:

| Local track | Catalog candidate | Catalog id written | Metadata written | Import result | Synced lyrics |
| --- | --- | --- | --- | --- | --- |
| `Song` | `Song`, same artist/album/duration | `123456` | `TXXX:ITUNESCATALOGID` + usual tags | not verified | not verified |
| `song` | `SONG (Remastered 2011)` | `123456` | as above | not verified | not verified |
| `Song (Live)` | `Song (Live at Wembley)` | `99` | as above | not verified | not verified |
| `Song` | `Song (Live)` | none | nothing extra | n/a | n/a |
| `Song` | `Song` by another artist | none | nothing extra | n/a | n/a |
| `Song` | `Song`, 7:00 long | none | nothing extra | n/a | n/a |
| `Song` | two identical catalog entries | none (ambiguous) | nothing extra | n/a | n/a |
| `Song` | provider unreachable / offline | none | nothing extra | n/a | n/a |

Manual check a maintainer can run (not automated, needs a real Music library):
enable the option, download a few tracks of different kinds (a plain studio
track, a remaster, a live take, a track that only exists as a remix), import the
files into Music, then record for each whether Music shows the catalog artwork
and Apple's timed lyrics instead of a plain-text `USLT`. Repeat once with the
option disabled as a control.

### What could not be verified

Two of the spec's success criteria need an Apple Music client and a real
account, neither of which exists in this development environment:

- whether Music actually **reads** `ITUNESCATALOGID` on local-library import
- whether Apple's **native synced lyrics** appear as a result

The mechanism may also be ignored, renamed, or deliberately broken by a future
Music release. Until those two are confirmed on a real library, the feature is
**not production-ready**: it stays disabled by default, and a miss (or a changed
Apple behavior) only ever means a file without a catalog id. Failing safely is
the one property that *is* verified here — every lookup failure, timeout, bad
payload, or outage leaves the download, the metadata, and the lyrics untouched.
