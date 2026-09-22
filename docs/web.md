# Web Application

Screenshot references and capture requirements are maintained in the
[screenshot plan](screenshots.md). The playlist selection and processing
screenshots are embedded beside their corresponding sections below; keep
personal paths, credentials, cookies, and library names out of every capture.

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

The playlist selection page presents each parsed playlist as an independent
button/card. Checkboxes also support Select all, Deselect all, and
**Download selected playlists**. The batch flow reuses the single-playlist
resolver, cache, download, and M3U pipeline for every selected playlist while
sharing resolved audio across the batch. Each playlist receives its own M3U
download and completion status. After a result is available, **Download another playlist**
returns to that same selection state for the upload job; the ZIP is not
uploaded or parsed again. The existing M3U download remains available from
each result.

![Playlist selection and batch controls](images/playlist-selection.png)

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

![Live processing progress](images/processing-progress.png)

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

The finished result is available at `GET /processing/<job_id>/<playlist_id>/result`,
which renders per-track outcomes (status and reason) alongside the summary
counts. While the job is still queued or running, that route redirects to the
live processing page.

The track list can be filtered between **All tracks** and **Failed or
ambiguous**, which shows exactly the tracks the summary counts under *Failed*
and *Ambiguous*. Tracks that are only missing, rejected, or uncertain stay in
the **All tracks** view. The filter is client-side and is offered only when at
least one track is failed or ambiguous. The retry action below is deliberately
wider: it re-runs every track that did not resolve locally or by download.

When a finished result still has unresolved tracks, the page offers
**Retry N unresolved tracks**, which posts to
`POST /processing/<job_id>/<playlist_id>/retry`. A retry re-runs the full
resolution pipeline for those tracks only: tracks that already matched locally
or downloaded keep their result, so no audio is downloaded twice and already
resolved entries stay in the playlist. The M3U is rewritten from the merged
results, and the browser returns to the live processing page, which comes back
to the refreshed result page when the retry settles. A retry is rejected while
the job is still running and is a no-op when nothing is unresolved.

## M3U Download

When processing completes, offer a `Save playlist (M3U)` link to
`GET /processing/<job_id>/<playlist_id>/playlist.m3u`, which downloads the
generated playlist as an attachment.

## In-App Update Notice

Every page runs `GET /update/check`, which asks GitHub for the latest
`p55d2k/spotm3u` release (cached server-side for `[update] check_interval_hours`,
default 24 hours) and reports whether a newer version than the running
`spotm3u.__version__` exists. The check is non-fatal: network failures, rate
limits, missing releases and unknown assets all report "no update" without
raising, so an offline or stale machine works exactly as before.

When an update is available, the header shows a banner with the new version, a
**Release notes** link, a dismiss control (remembered per version in
`localStorage`), and **Download update**. SpotM3U never replaces a running
application on its own - a packaged bundle cannot safely overwrite its own
files - so the action downloads the correct platform installer instead:

- macOS downloads `SpotM3U-<version>-macos-<arch>.pkg`,
- Windows and Linux download `SpotM3U-<version>-<platform>-<arch>.zip`.

In the desktop shell the download goes through the `WindowControls`
`download_update` bridge into the Downloads folder with an **Open folder** toast
(the same flow as saving an M3U). The toast shows the same layout as the
playlist-save toast: the downloaded filename plus the next step, *quit SpotM3U
and open the file to install*. A finished download also clears the banner and
remembers the dismissal for that version, so the notice stops prompting once the
installer is on disk. In a plain browser the asset URL opens in a new tab.
Downloads travel over HTTPS to the GitHub release asset URL only and are never
launched or extracted by the app.

The check can be disabled wholesale with `[update] check = false` in
`config.toml`, and its repository overridden with `[update] github_repo`.

## Add to Media Player

A completed result with at least one resolved track also offers **Add to Media
Player**, which posts to
`POST /processing/<job_id>/<playlist_id>/media-player`. The button label is the
same on every platform; the behavior is platform-aware behind one application
interface (`spotm3u.media_player.add_to_media_player`).

The action always uses the playlist the processing job already generated. It is
never regenerated for the import, and the M3U download stays available
independently of it.

### macOS

On macOS the action adds to Apple Music. The app uses the system `osascript`
command to ask the Music app to create or reuse a user playlist and add each
resolved local file in playlist order, because Music has no supported
playlist-file import. Each entry is added separately so duplicates are
preserved where Music permits them. Resolved files retain the individual
artist values in their ID3 metadata, so collaborations are imported as
collaborations rather than one concatenated artist name, and Music reads the
title, artist, and album from each file.

When a user playlist with the same name already exists in Music, the app asks
the user (via an AppleScript dialog) whether to **Add all** (append every
file, creating duplicates), **Skip duplicates** (only add files whose location
is not already in that playlist, so re-importing the same M3U twice does not
double-add), or **Cancel**. A cancelled import changes nothing. The playlist
is revealed in Music as a best effort, but Music may not come to the
foreground, so the result page also tells the user to open Apple Music and
look for the playlist in the Library sidebar. Music reports per-entry errors
back to the app; unresolved tracks and import failures are reported as partial
results rather than being claimed as successful.

### Windows

Windows has no Apple-Music-equivalent playlist-import API, so the action stays
standards-based: the generated `.m3u` is opened through its default file
association with `os.startfile`, and any installed player that understands
M3U (VLC, foobar2000, Windows Media Player, MusicBee, and so on) imports it.
The playlist path is resolved with `pathlib` and passed to the association as
a single argument, so drive letters, spaces, and Unicode characters survive
and no shell is involved. Entries inside the playlist keep the forward slashes
described in [M3U generation](m3u.md), which Windows players read normally. The
result page confirms with *Playlist opened in your default media player.*

### Other platforms

The action is offered only on macOS and Windows. Everywhere else the manual
M3U download keeps working, and no media-player integration is required for
the playlist to be generated.

The M3U and any downloaded MP3s are written to the persistent download
directory so the playlist continues to reference real local files.

## Download Directory

Downloads are stored persistently under `DOWNLOAD_DIR` config, defaulting to
`<MUSIC_LIBRARY>/SpotM3U-downloads/`. This keeps files so the M3U can load
them and lets later runs match them locally instead of re-downloading.

## Important

A track should only be marked successful after the final local audio file has passed the relevant validation.

A track should not be marked failed merely because its metadata was imperfect.

![Completed result page](images/result-page.png)
