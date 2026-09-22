# Matching and Source Validation

## Purpose

The project must associate each Spotify/Exportify Track with the correct recording.

There are two resolution paths:

1. Existing local audio matching
2. Online source selection followed by downloading

## Local Matching

The local resolver handles matching Spotify/Exportify metadata against existing local audio files.

The local resolver should remain independent from online source selection.

## Online Matching

For tracks without a suitable local file, the application searches online sources.

The online pipeline is:

Track
→ search candidates
→ rank candidates
→ reject obviously unsuitable candidates
→ download plausible candidate
→ validate downloaded audio
→ accept/reject result

## Core Principle

Online matching should be **permissive during discovery and conservative during validation**.

The system should not require perfect metadata before downloading.

A candidate with incomplete metadata may still be the correct recording.

Conversely, a candidate with perfect-looking metadata may still contain unwanted dialogue or sound effects.

Therefore:

**metadata match ≠ guaranteed correct audio**

and:

**missing metadata ≠ incorrect audio**

## Candidate Ranking

Ranking evaluates two independent questions:

- **A. Recording identity** — is this the requested song by the requested artist?
- **B. Source quality** — is this upload a good way to extract clean song audio?

A wrong artist outweighs every title/source-quality advantage. The
source-quality preference operates only among candidates that already appear
to be the correct recording.

Signal components:

- **artist identity** (first-class, ordering-dominant)
- title similarity
- duration similarity
- version compatibility
- source type
- source-quality tier (audio suitability)
- uploader/channel (supporting evidence for artist identity)
- obvious content indicators
- penalty for music-video intent

Artist identity is not a small score component. For common song titles (for
example Joker Xue's `演员`), a candidate with a conflicting artist must be
rejected even when the title is an exact match, and a candidate whose artist
identity is confirmed must outrank a same-title candidate that only shares the
song name.

No individual signal other than a confirmed wrong artist should normally be a
hard requirement.

## Source Discovery

Discovery runs **every** configured search query and aggregates the results
before ranking. A candidate that appears only in a later query is still
discovered, and duplicates across queries (by URL) are removed. Discovery
never chooses a "best" candidate and never downloads; selection happens after
all queries have been ranked.

Search queries are a small, configurable set, always paired as
`{artist} {title}` with a few focused audio hints:

- `{artist} {title}`
- `{artist} {title} official audio`
- `{artist} {title} audio`
- `{artist} {title} official`
- `{title} {artist}`

Lyric-video queries are deliberately not part of this set: a lyric video is a
low-signal audio source, and lyrics metadata is retrieved by a dedicated lyrics
library rather than searched for online. Lyric-titled uploads that still appear
in the results of the queries above remain recognized and ranked below pure
audio sources.

Title-only queries are used only as a fallback when artist information is
genuinely absent, because a title-only search tends to return same-title
uploads by other artists.

## Source-Quality Preference

Recording identity includes explicit version attributes. Instrumental targets
must prefer instrumental candidates; an explicitly vocal candidate is strong
contradictory evidence and is rejected. Missing version metadata lowers
confidence but does not prove a mismatch. Instrumental searches include
instrumental variants.

Preference order for the **same correct recording** (best first):

1. official audio / audio upload
2. lyric video / lyrics
3. clean official song upload
4. official music video
5. other legitimate music upload
6. live / performance (only when the requested recording is a live/performance)
7. covers / remixes / mashups (only when explicitly requested)

This is a ranking preference, not an identity rule. A high-quality source for
the wrong artist never beats a lower-quality source for the correct artist.
An official music video remains a valid fallback when no audio/lyrics source
for the same recording is available.

Intent is detected from title, uploader/channel, artist/creator metadata,
description, tags, and duration. Positive signals include `official audio`,
`lyrics`, `audio`, and the artist name or an official/verified channel.
Negative signals include `cover`/`翻唱`, `karaoke`, `remix`, `sped up`,
`slowed`, `nightcore`, `8D`, `live`/`现场`, `concert`, `reaction`,
`interview`, `trailer`, `movie`, `scene`, and `compilation`. Multilingual
indicators are normalized where practical.

CJK scripts are unified before comparison: Traditional Chinese titles such as
`薛之謙 / 演員【歌詞】` compare equal to the Simplified `薛之谦 / 演员【歌词】`
equivalent, so script variants never bleed into title or identity scoring.

## Title Matching

Normalize titles before comparison.

Handle:

- case
- punctuation
- whitespace
- Unicode variations
- Traditional/Simplified CJK script differences
- common separators
- version suffixes
- `Official MV` / `Official Audio` / `MV` / `歌词` labels

Treat core song identity separately from version information, upload labels,
and the requested artist attribution.

A title like `薛之谦 演员 Official Music Video` and a title like
`薛之谦 演员 [歌词]` normalize to the same underlying recording identity, and a
bare `演员` is **not** automatically better than `薛之谦 演员` because the
artist attribution is itself identity evidence. The requested artist's name is
stripped from title cores before similarity so an artist attribution in the
title is never scored as a title difference, and leftover non-title tokens
(such as romanized artist names like `Joker Xue`) do not hide a full requested
title contained in the candidate core.

Examples:

`Wonderwall`

`Wonderwall - Remastered`

`Wonderwall (Remastered)`

may represent the same underlying song, as may:

`演员`

`演员 Official MV`

`演员 Official Audio`

## Artist Matching

Artist identity is a first-class signal assessed across three evidence tiers:

1. explicit artist metadata
2. title attribution (the artist name appearing in the title)
3. uploader/channel name

Track artist metadata remains a list of individual credited artists. Search
queries join those values with spaces only at the query boundary, and plain
text displays use comma separators. ID3 metadata writes each artist as a
separate TPE1 value so collaborations remain recognizable to music players and
Apple Music.

A confirmed identity (explicit artist or title attribution) is the strongest
evidence and weights higher than a mere exact title match. Uploader/channel
matching is supporting evidence and does **not** need to equal the Spotify
artist exactly.

Artist matching accounts for:

- multiple artists
- featured artists
- `feat.`
- `ft.`
- `with`
- minor formatting differences
- CJK artist names and channel suffixes such as `薛之谦官方频道`

Behavior:

- A candidate with an explicit artist that conflicts with the requested
  artist is rejected when the title otherwise matches (wrong artist outweighs
  title similarity).
- A candidate whose requested artist appears anywhere (explicit, title, or
  uploader/channel) is accepted even when other metadata is imperfect.
- A candidate with no artist evidence stays plausible but can never outrank a
  candidate with confirmed identity.
- The uploader/channel differing from the artist is not, by itself, a reason
  to reject.

Artist evidence is also drawn from description and tags, but uploader/channel
and description/tags are supporting evidence only and are never treated as
proof.

Explicit cover indicators (`cover`, `翻唱`) are strong negative signals and
reject the candidate unless the requested artist is the performer.

## Duration

Duration is a useful signal, not a strict equality requirement.

Allow reasonable differences caused by:

- different masters
- intros/outros
- silence
- platform trimming
- metadata rounding
- remastering

A missing duration should not automatically reject a candidate.

A large duration mismatch can be strong evidence against a candidate.

## Version Information

Version markers must be interpreted rather than blindly compared.

Examples:

- Remastered
- Live
- Acoustic
- Radio Edit
- Extended
- Deluxe
- Single Version

A missing version marker does not automatically mean the source is wrong.

A clear conflicting version should receive a strong penalty or rejection.

Example:

Requested:
`Song - Remastered`

Potentially acceptable:
`Song`

Clearly conflicting:
`Song - Live`

## Source Type

Prefer standalone music/audio sources.

Penalize or reject obvious:

- music videos
- movie scenes
- trailers
- interviews
- podcasts
- reactions
- compilations
- covers
- karaoke
- remixes
- mashups
- sped-up/slowed versions

However, incomplete source metadata should not itself cause rejection.

## Actual Audio Requirement

The desired result is the actual music recording.

A source can have correct title and artist metadata while containing:

- dialogue
- cinematic effects
- scene audio
- crowd noise
- unrelated speech

Therefore the application must perform downloaded-audio validation after yt-dlp finishes.

Downloaded-audio heuristics distinguish hard evidence of spoken dialogue from
warning-only musical textures such as soundtrack, ambience, and sound effects.
Warnings are logged without automatically failing a playable cinematic or
instrumental file; clear spoken-dialogue evidence remains a hard failure.

## Downloaded Audio Validation

Downloaded audio should be checked for:

- readable audio stream
- expected duration
- format
- obvious silence
- obvious interruptions
- obvious speech/non-music sections where technically detectable

This is a second validation layer.

## Candidate Retry

If multiple plausible candidates exist:

1. Rank them.
2. Try the strongest candidate.
3. Download it.
4. Validate the resulting audio.
5. If validation fails, try another plausible candidate when appropriate.

This is preferable to failing immediately after one bad source.

## Debug Logging

Ranking logs (at DEBUG level) each candidate decision with its score, title
similarity, the artist-evidence breakdown (explicit / title / uploader), and
the reasons for accepting or rejecting. This makes wrong-artist or cover
miscounts like the `演员` case traceable.

## Confidence

Use confidence to decide whether a candidate is plausible.

Do not interpret low confidence as proof of incorrectness.

Suggested conceptual states:

### Strong

Multiple signals agree and no major conflict exists.

### Plausible

Some metadata is missing or imperfect, but nothing strongly indicates the wrong recording.

### Uncertain

Important information conflicts or multiple substantially different recordings are plausible.

### Rejected

There is strong evidence that the source is incorrect or unsuitable.

## Ambiguous

Use `ambiguous` only for genuine ambiguity.

Do not mark a candidate ambiguous merely because:
- duration is unavailable
- uploader differs
- metadata is incomplete
- title contains harmless extra text
- version formatting differs

## Conservative Behavior

The system should be conservative about **accepting obviously bad audio**, not conservative about **finding any plausible candidate at all**.

The desired tradeoff is:

false positive after audio validation
>
false negative caused by missing metadata

but the system should still avoid downloading obviously unsuitable sources.

## Important Limitation

Automatic detection of "music only" is imperfect.

The application cannot guarantee that an arbitrary recording contains no speech or sound effects.

It should detect obvious problems and expose uncertainty rather than pretending the classifier is omniscient.
