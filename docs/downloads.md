# Downloads

## Purpose

The project downloads online sources for tracks that cannot be resolved from the existing local audio library.

Downloaded audio becomes local MP3 files that can be referenced by the generated M3U.

## Pipeline

Track
→ source search
→ candidate ranking
→ source validation
→ yt-dlp
→ MP3
→ downloaded-audio validation
→ resolved local file

## Important

Source validation should not be so strict that every imperfect candidate is rejected before downloading.

A plausible candidate may need to be downloaded before the application can determine whether its actual audio is suitable.

## Candidate Retry

When several plausible candidates exist:

1. Download the highest-ranked candidate.
2. Validate the resulting audio.
3. If invalid, try another plausible candidate.
4. Stop when a valid recording is found or candidates are exhausted.

Do not retry obviously unsuitable candidates.

## yt-dlp

Use yt-dlp for downloading.

Reuse existing downloader code where practical.

The downloader should:

- accept a source URL
- download the source
- extract/convert audio
- produce MP3
- store output in a controlled directory
- report failures
- detect incomplete output

## ffmpeg

Use ffmpeg for audio extraction/conversion where required by the selected yt-dlp format.

## Output Naming

Use filesystem-safe deterministic filenames.

Do not use raw metadata as shell commands.

## Temporary Files

Temporary yt-dlp/ffmpeg files must remain inside controlled job directories.

## Concurrency

Use bounded concurrency.

Avoid spawning an uncontrolled number of yt-dlp or ffmpeg processes.

## Validation

Successful yt-dlp execution does not automatically mean successful track resolution.

The resulting audio must pass downloaded-audio validation before being included in the final playlist.

## Cache

Successful downloads may be cached and reused when their association with the requested recording is sufficiently strong.

Cache reuse must not rely solely on filenames.
