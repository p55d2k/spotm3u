# Web Application

SpotM3U is a React single-page application served by Flask. In development,
`uv run dev` starts Vite and Flask together; in a production build, Flask serves
the compiled Vite output at `/`. The React client communicates with Flask
through the JSON API documented in [api.md](api.md).

```text
Exportify ZIP
→ import
→ playlist selection
→ processing
→ result
→ M3U download or media-player import
```

![SpotM3U demo workflow](images/demo.gif)

## Import

The import screen accepts an Exportify ZIP through a browser file picker or
drag-and-drop. In the desktop shell, **Choose ZIP** opens the native operating
system file dialog through the `WindowControls.choose_zip` bridge. The selected
path stays in the shell and is handed to Flask through
`POST /api/upload/picked`; the browser never submits an arbitrary local path.

## Playlist selection and processing

After import, SpotM3U displays the playlists found in the export. A single
playlist can be opened directly, or multiple playlists can be selected and
converted together. Each selected playlist gets independent progress, result,
and M3U output while shared audio resolution avoids duplicate work.

Processing resolves local audio first, then searches, ranks, validates, and
downloads an online match when necessary. Fast mode can skip slower enrichment
steps. Failed tracks can be retried from the processing screen.

## Results

The result screen reports resolved and missing tracks, offers the generated M3U
through the native save panel in the desktop shell, and can import a completed
playlist into the platform media player. Batch results expose the same actions
for each playlist.

The client-side routes are history-based, so Flask returns the React shell for
deep links without a file suffix. API errors remain JSON responses, including
for unknown `/api/...` paths.
