# Screenshot plan

The first three screenshots are included in `docs/images/`. Keep them current
with the application and do not use mockups or screenshots containing personal
data.

## Assets to add

Store the PNG files in `docs/images/` with these names:

| File | What it should show | Where to use it |
| --- | --- | --- |
| `result-page.png` | A completed result page with resolved, unresolved, and duplicate tracks visible | README, [web documentation](web.md) |
| `playlist-selection.png` | The playlist-selection page with multiple playlists and the batch-selection controls | [web documentation](web.md) |
| `processing-progress.png` | A live processing page showing meaningful per-track progress states | [web documentation](web.md) |

Use a 16:9 or 16:10 crop at about 1440×900 pixels. PNG is preferred for UI
text. Do not add a GIF or video unless a later UI interaction cannot be
understood from these static states.

## Capture requirements

- Use representative, non-personal sample playlist and track names.
- Hide filesystem paths, usernames, browser cookies, tokens, credentials, and
  private library names.
- Show the actual status labels and warnings; do not edit them into success
  states.
After updating the files, embed `result-page.png` near the README introduction,
and embed the other images beside the matching sections in `docs/web.md`.
Update this table if the UI or supported workflow changes.
