# Matching and Source Validation

## Purpose

The project must associate each Spotify/Exportify Track with the correct recording.

There are two resolution paths:

1. Existing local audio matching
2. Online source selection followed by downloading

## Local Matching

Task 10 handles matching Spotify/Exportify metadata against existing local audio files.

Its responsibility is to determine whether an existing local file represents the requested track.

Do not duplicate this logic inside the online resolver.

## Online Source Matching

For tracks without a suitable local file, the project searches online sources.

Online matching should compare:

- title
- artist(s)
- duration
- album
- version information
- uploader/channel
- source type

## Actual Recording Requirement

The desired result is the actual standalone music recording.

A source is not acceptable merely because it contains the requested song.

Examples of potentially unsuitable sources:

- music videos containing dialogue or scene audio
- movie clips
- trailers
- interviews
- reactions
- live performances
- covers
- karaoke
- remixes
- mashups
- sped-up/slowed versions
- fan edits
- videos with substantial cinematic effects

## Ranking

Candidate selection should use multiple signals.

Positive signals:

- title similarity
- artist similarity
- duration similarity
- expected version
- official/artist/audio-source indicators

Negative signals:

- music-video indicators
- live indicators
- remix/edit indicators
- cover indicators
- movie/scene indicators
- dialogue/interview indicators

No single signal should determine correctness.

## Duration

Duration is useful but not absolute.

Reasons for legitimate differences include:

- different masters
- intros/outros
- metadata rounding
- platform trimming
- silence at the beginning/end

A small difference should not automatically reject a source.

A large difference is a useful warning.

## Downloaded Audio

After yt-dlp downloads a source, validate the resulting audio separately.

This matters because metadata can look correct even when the source contains:

- speech
- dialogue
- sound effects
- crowd noise
- scene audio

Where technically possible, inspect the downloaded audio for obvious interruptions.

## Confidence

The system should distinguish:

### Strong

Metadata and source characteristics strongly agree with the requested recording.

### Uncertain

The candidate is plausible but important information is missing or contradictory.

### Rejected

The candidate clearly appears to be the wrong recording or unsuitable content.

## Conservative Behavior

False positives are worse than missing tracks.

Never silently turn a weak match into a successful result.

## Important Limitation

Automatic detection of "music only" is not perfect.

The system may detect obvious problems, but it cannot mathematically guarantee that an arbitrary recording contains no speech or sound effects.

The application should expose uncertainty rather than pretending otherwise.
