# Matching and source selection

Every Spotify track has to be matched to an actual recording. SpotM3U does this
in two ways:

1. **Local matching** — find the track among the audio files already on your
   computer.
2. **Online source selection** — when no local file matches, search, rank, and
   download a candidate recording.

The core principle is *permissive discovery, conservative validation*:
SpotM3U does not demand perfect metadata before trying a candidate, and it does
not trust perfect-looking metadata either. A candidate with incomplete metadata
can still be the right recording, and a candidate that looks right on paper can
still contain dialogue or sound effects. So **a metadata match is not a
guaranteed-correct audio file**, and **missing metadata does not make audio
wrong**.

## Local matching

The local resolver searches your existing audio files (MP3, FLAC, and friends)
and matches them against the exported track metadata. It is a separate,
independent strategy from online source selection — a track found locally never
goes through the online pipeline.

The scanned extensions are configurable: `[library] extensions` in
`config.toml` lists them (comma-separated, case-insensitive, dot optional), and
an empty value keeps the built-in list. Widen it when a track is reported
missing although the file is in the library in a format the default list does
not cover.

## Online source selection

For tracks without a local match, the online pipeline is:

```text
Track
 → search candidates
 → rank candidates
 → reject obviously unsuitable candidates
 → download the best plausible candidate
 → validate the downloaded audio
 → accept or reject the result
```

### Candidate ranking

Ranking evaluates two independent questions:

- **A. Recording identity** — is this the requested song by the requested
  artist?
- **B. Source quality** — is this upload a good way to extract clean song
  audio?

Artist identity is first-class. A confirmed wrong artist outweighs every title
and source-quality advantage; the source-quality preference only operates among
candidates that already appear to be the correct recording. A title made only of
symbols has no text to compare, so it is compared through the name it expands
into (`❤️` against `Heart`), which is how the artist's own upload of such a
track is recognized; the artist, duration and version checks are unchanged. This matters for
common song titles (for example Joker Xue's `演员`): a candidate whose artist is
confirmed outranks a same-title candidate that only shares the song name, and a
conflicting artist is rejected even on an exact title match.

Ranking weighs, among other signals:

- artist identity (explicit metadata, title attribution, uploader/channel)
- title similarity
- duration similarity
- version compatibility (live, acoustic, remastered, ...)
- source type and source-quality tier
- uploader/channel as supporting identity evidence
- obvious content indicators (cover, karaoke, interview, trailer, ...)
- a penalty for music-video intent

Container signals such as `Official Audio`, `lyrics`, and the artist's official
channel are positive; `cover`/`翻唱`, `karaoke`, `remix`, `live`, `reaction`,
`interview`, `trailer`, and `movie` are negative. CJK titles are unified before
comparison so a Traditional-Chinese `薛之謙 / 演員【歌詞】` compares equal to its
Simplified equivalent, and artist names in titles are normalized so attribution
is never scored as a title difference. A title like `薛之谦 演员 Official Music
Video` and `薛之谦 演员 [歌词]` resolve to the same underlying recording.

### Source quality

For the **same correct recording**, sources are preferred in this order:

1. official audio / audio upload
2. lyric video / lyrics
3. clean official song upload
4. official music video
5. other legitimate music upload
6. live / performance (only when the requested recording is live)
7. covers / remixes / mashups (only when explicitly requested)

This is a ranking preference within the correct recording, not an identity
rule. An official music video is a valid fallback when no audio/lyrics source
exists, but it never outranks an audio source for the same recording. Lyric
videos are not searched for deliberately — a lyric video is a low-signal audio
source and lyrics metadata comes from a dedicated lyrics provider — though one
that surfaces anyway loses to a pure audio upload.

### Search queries

Discovery runs **every** configured query and aggregates the results before
ranking, so a candidate that appears only in a later query is still found and
duplicates (by URL) across queries are removed. Discovery never picks a "best"
candidate itself and never downloads. Queries are a small, configurable set,
always paired as `{artist} {title}` with a few focused audio hints (`official
audio`, `audio`, `official`). Title-only queries are a fallback for when artist
information is genuinely absent, because a title-only search tends to return
covers by other artists.

A title is not always a searchable string, so hard-to-search titles are
expanded. A track called `❤️` or `♾️` (Coldplay has both) is searched for as
written *and* as `{artist} heart` / `{artist} red heart` / `{artist} infinity`;
an emoji used as decoration is named the same way (`My Universe ❤️` also
searches `my universe heart`). Expansion **supplements** the original: the
queries for the title as written always run first (and are what fast mode tries
first), only a couple of expansions are added per track, and they come from a
small curated table of symbols plus a generic fallback that derives a phrase
from the character's Unicode name. Symbols with no search value — note glyphs,
typographic signs — are never turned into queries like `musical note`. Full-width
and compatibility variants (`Ａｖｉｃｉｉ`) are folded to their plain form, and
non-Latin titles are kept exactly as written: they are never transliterated or
approximated. An ordinary title produces the same queries it always did.

## Duration and version

Duration is a useful signal, not a strict equality check — different masters,
intros/outros, silence, and platform trimming all shift it. A large mismatch is
strong evidence against a candidate, but a missing duration never rejects one.

Version markers are interpreted rather than compared blindly. Requesting
`Song - Remastered` can be satisfied by a plain `Song`, but a `Song - Live`
version is a real conflict.

## Downloaded audio validation

The desired result is the actual music recording. A source can have correct
title and artist metadata yet contain dialogue, cinematic effects, crowd noise,
or unrelated speech, so after `yt-dlp` finishes, SpotM3U validates the audio
it actually got: readable stream, expected duration, format, obvious silence,
obvious interruptions, and obvious speech where speech is detectable.

Speech detection is deliberately calibrated: cinematic and instrumental
soundtracks can contain ambience, impacts, and vocal-like textures, so those
are warnings, while clear spoken dialogue is a hard failure. The system is
honest about the limit of automatic "music only" detection — it finds obvious
problems and reports uncertainty rather than pretending to be infallible.

## Candidate retry

When several plausible candidates exist, SpotM3U tries them one at a time:
download the strongest, validate the result, and move to the next if it is
rejected. A single bad source does not fail the track.

## Outcome categories

- **Strong** — multiple signals agree and nothing conflicts.
- **Plausible** — some metadata is missing or imperfect, but nothing strongly
  points to the wrong recording.
- **Uncertain** — important information conflicts, or several substantially
  different recordings are plausible.
- **Rejected** — strong evidence that the source is incorrect or unsuitable.

`Ambiguous` is reserved for genuine ambiguity. It is not used merely because a
duration is missing, an uploader differs, metadata is incomplete, a title has
harmless extra text, or a version is formatted differently. The overall goal is
to be conservative about accepting obviously bad audio, not conservative about
finding plausible candidates at all.

## Diagnostics

Candidate decisions are logged at debug level with the score, title similarity,
the artist-evidence breakdown (explicit / title / uploader), and the accept or
reject reasons, so wrong-artist and cover miscounts stay traceable. Logs at
`SPOTM3U_LOG_LEVEL=DEBUG` show the full picture.