# Downloads

## Purpose

The project downloads online sources only after source selection and validation.

The downloaded audio becomes a local file that can be referenced by the generated M3U.

## Pipeline

Track
→ source search
→ candidate ranking
→ source validation
→ yt-dlp
→ audio extraction/conversion
→ MP3
→ downloaded-audio validation
→ resolved local file

## yt-dlp

The project uses yt-dlp for online source downloading.

Existing yt-dlp code should be reused where practical.

The downloader should:

- accept a validated source URL
- download the source
- extract audio
- convert to MP3 using ffmpeg where necessary
- return a controlled local path
- report failures explicitly

## Output Naming

Output filenames must be filesystem-safe.

Do not use raw Spotify titles/artists directly as shell commands or filesystem paths.

Avoid collisions between tracks with similar names.

## Temporary Files

yt-dlp/ffmpeg temporary files must remain inside controlled job directories.

Temporary files should be removed after successful processing when they are no longer needed.

## Concurrency

Downloads should use bounded concurrency.

Do not create an unlimited number of simultaneous yt-dlp/ffmpeg processes.

## Validation

A successful yt-dlp process does not automatically mean the final track is valid.

The resulting audio must pass downloaded-audio validation before being included in the final M3U.

## Cache

Successfully resolved downloads may be reused for later jobs when their track/source identity is sufficiently strong.

Caching must not rely only on filenames.
