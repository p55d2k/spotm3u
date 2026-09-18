# Matching and Source Validation

## Purpose

The project must associate each Spotify/Exportify Track with the correct recording.

There are two resolution paths:

1. Existing local audio matching
2. Online source selection followed by downloading

## Local Matching

Task 10 handles matching Spotify/Exportify metadata against existing local audio files.

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

Ranking should combine signals including:

- **artist identity** (first-class, ordering-dominant)
- title similarity
- duration similarity
- version compatibility
- uploader/channel (supporting evidence for artist identity)
- source type
- obvious content indicators

Artist identity is not a small score component. For common song titles (for
example Joker Xue's `演员`), a candidate with a conflicting artist must be
rejected even when the title is an exact match, and a candidate whose artist
identity is confirmed must outrank a same-title candidate that only shares the
song name.

No individual signal other than a confirmed wrong artist should normally be a
hard requirement.

## Title Matching

Normalize titles before comparison.

Handle:

- case
- punctuation
- whitespace
- Unicode variations
- common separators
- version suffixes
- `Official MV` / `Official Audio` / `MV` labels

Treat core song identity separately from version information and upload labels.

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
