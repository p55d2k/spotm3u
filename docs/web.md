# Web Application

## Technology

Use Flask with:

- Jinja2
- vanilla HTML/CSS/JS unless existing project requirements require otherwise

## User Flow

Exportify
→ ZIP upload
→ playlist selection
→ processing
→ result
→ M3U download

## Processing

Each Track goes through:

1. Local audio resolution
2. Online search if no local match exists
3. Candidate ranking
4. Source validation
5. yt-dlp download
6. Downloaded-audio validation
7. Retry with another candidate if appropriate
8. Final resolution

## Processing States

Useful states include:

- queued
- resolving-local
- searching
- ranking
- validating-source
- downloading
- validating-audio
- retrying-source
- complete
- failed
- ambiguous

## Important UI Behavior

Do not show:

"Failed: no candidate passed source validation"

when the system simply has not yet attempted plausible candidates.

Differentiate:

- no plausible candidates found
- candidate rejected before download
- download failed
- downloaded audio failed validation
- multiple candidates remained ambiguous

## Progress

For each track, show the current meaningful stage.

Examples:

- Finding local audio
- Searching for source
- Checking source
- Downloading
- Checking downloaded audio
- Trying another source

## Result

Display:

- total tracks
- successful tracks
- local matches
- downloaded tracks
- failed tracks
- ambiguous tracks
- rejected tracks
- uncertain tracks

For failures, display the actual processing reason.

## Important

A track should only be marked successful after the final local audio file has passed the relevant validation.

A track should not be marked failed merely because its metadata was imperfect.
