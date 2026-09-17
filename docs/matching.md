# Local Audio Matching

## Purpose

Match Spotify/Exportify track metadata against audio files already present in the user's local music library.

The resolver must prioritize both **identity correctness** and **audio-content correctness**.

A file is not considered a valid match merely because its title and artist metadata appear correct. The actual audio should represent the intended music track without unwanted cinematic, dialogue, sound-effect, or other non-musical interruptions.

This layer is independent of Spotify authentication and Exportify parsing.

---

## Input

The resolver receives generic `Track` objects.

Example:

```text
Title: Song Name
Artists: [Artist Name]
Album: Album Name
Duration: 213000
```

It searches a local audio library for a file containing the intended recording.

---

## Matching Strategy

Use progressively less strict matching:

```text
1. Exact metadata
        ↓
2. Normalized artist + title
        ↓
3. Filename-based matching
        ↓
4. Fuzzy matching
        ↓
5. Audio/content validation
        ↓
6. Missing / ambiguous
```

Do not begin with expensive fuzzy matching when exact indexed matching can solve the metadata portion.

However, metadata matching alone does **not** guarantee that the audio itself is the desired recording.

---

## Identity vs Audio Quality

Treat these as separate checks:

### Identity

Does this file appear to correspond to the requested:

- title
- artist
- recording/version
- album where useful

### Audio Content

Does the actual recording contain the intended music without unwanted interruptions?

A candidate should not be considered a fully valid match solely because its metadata is correct.

---

## Unwanted Audio Content

The resolver should avoid candidates containing non-musical content such as:

- cinematic sound effects
- movie dialogue
- spoken narration
- character dialogue
- advertisements
- extended sound-design sequences
- scene audio
- crowd/interview audio where it is not part of the intended recording
- dramatic interruptions inserted into otherwise continuous music

This is especially important for tracks sourced from music videos, movie scenes, trailers, live visual productions, or other video-derived recordings.

For example, if a video contains:

```text
music → dialogue → sound effect → music
```

and the requested track is the clean musical recording, that file should not be treated as an equivalent match merely because the title and artist are correct.

---

## Preferred Recording

When multiple candidates represent the same song, prefer the candidate that most closely corresponds to the intended standalone musical recording.

Potentially useful signals include:

- clean/official audio metadata
- expected duration
- absence of obvious dialogue or sound-effect sections
- consistent audio throughout the recording
- album/release metadata
- recording/version information
- filename indicators

Do not assume that a particular source is always clean.

---

## Duration

Duration is a useful matching signal but should not be treated as absolute.

Small differences may result from:

- encoding
- silence at the beginning/end
- different releases
- remasters
- explicit/clean versions
- metadata inaccuracies

Large unexpected differences should reduce confidence and may indicate that the candidate is a different version or contains additional video/cinematic material.

Duration should therefore contribute to candidate scoring rather than blindly rejecting every non-identical duration.

---

## Audio-Content Validation

If the project has access to suitable local audio-analysis capabilities, use them to detect suspicious interruptions.

Potential signals include:

- speech-like segments
- abrupt non-musical noise
- unusually large changes in spectral characteristics
- extended low-information/silence regions
- sudden sound-effect-like sections
- significant deviations from the expected musical structure

Audio analysis should be treated as a **validation/confidence signal**, not as a perfect classifier.

Do not claim that a file is clean solely because an automated detector failed to identify an interruption.

---

## Important Limitation

Metadata and filenames cannot reliably determine whether audio is clean.

For example:

```text
Title: Example Song
Artist: Example Artist
```

could refer to:

- the official studio recording
- a music video audio track
- a movie scene version
- a live performance
- a remix
- an instrumental
- a version containing dialogue
- a version containing cinematic sound effects

Therefore, when multiple candidates exist, the resolver should consider recording/version information and audio-content signals where available.

---

## Normalization

Normalization may include:

- Unicode normalization
- case folding
- punctuation normalization
- whitespace normalization
- common separator normalization
- common filename formatting differences

Original metadata must remain unchanged.

Normalized values are only for matching.

---

## Artists

Artist matching should account for multiple artists.

Do not blindly flatten every artist string into one value if doing so makes matching less reliable.

The implementation should define consistent behavior for:

```text
Artist A
Artist A, Artist B
Artist A feat. Artist B
Artist A & Artist B
```

without modifying the original track metadata.

---

## Local Metadata

Use `mutagen` or the existing local metadata implementation to inspect audio files.

The library should cache metadata rather than rereading the same file repeatedly.

Where practical, collect:

- title
- artist
- album
- duration
- album artist
- track number
- disc number
- version/remix information
- filename
- file path

Do not require every field to exist.

---

## Match Results

The resolver should distinguish at least:

```text
MATCHED
MISSING
AMBIGUOUS
```

A matched result should identify exactly one local path.

An ambiguous result should contain candidate paths and, where possible, explain why they were not confidently distinguishable.

A missing result should retain the original track.

---

## Confidence

Matching should be treated as a confidence problem rather than simply:

```text
metadata matches → use file
```

A candidate can have:

```text
high metadata confidence
low audio-content confidence
```

and therefore should not automatically become a final match.

The implementation should make the reasoning behind candidate selection inspectable.

---

## Fuzzy Matching

Use RapidFuzz only after stronger matching strategies fail.

Fuzzy matching should:

- operate on sensible candidate sets
- use a documented threshold
- preserve ambiguity when multiple candidates are similarly plausible
- never silently choose a poor candidate

The threshold should be configurable rather than scattered throughout the code.

---

## Library Index

Do not scan the entire music directory once for every playlist track.

Build an index such as:

```text
normalized artist + title
        ↓
candidate local files
```

Potential additional indexes may include:

- normalized filename
- album
- album artist
- recording/version indicators

---

## Performance

Preferred order:

```text
Indexed exact lookup
        ↓
Indexed normalized lookup
        ↓
Restricted filename lookup
        ↓
Metadata/version filtering
        ↓
Fuzzy matching
        ↓
Audio-content validation where necessary
```

Do not perform expensive audio analysis on every file if cheaper matching signals can eliminate unsuitable candidates first.

Profile before introducing complicated optimization.

---

## False Positives

A false positive is worse than a missing match.

For example:

```text
Requested:
Song A - Artist A

Candidate:
Song A - Artist A
but contains movie dialogue halfway through
```

This should not automatically be accepted as the requested clean recording.

A missing track can be reported.

A wrong or interrupted recording silently appearing in the generated playlist is much harder to notice.

---

## Result Semantics

A track should only be classified as `MATCHED` when the resolver has sufficient confidence that:

1. It is the intended song/recording.
2. The metadata is sufficiently consistent.
3. There is no known conflicting version information.
4. The audio is not obviously contaminated by unwanted dialogue, cinematic effects, or other non-musical interruptions when such validation is available.

Otherwise classify the result as `AMBIGUOUS` or `MISSING` as appropriate.

---

## User Visibility

When a candidate is rejected because it appears to be a non-clean or otherwise unsuitable version, the UI should eventually be able to communicate this distinction.

For example:

```text
Song A
✓ Metadata match
✗ Possible dialogue/cinematic interruption
```

Do not hide important matching uncertainty from the user.

---

## Safety Principle

Never optimize matching solely for the number of matched tracks.

The goal is:

```text
correct song
+
correct recording/version
+
clean intended audio
```

not merely:

```text
filename looks right
```
