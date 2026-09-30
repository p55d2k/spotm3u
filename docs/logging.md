# Logging and crash reports

SpotM3U writes diagnostics to a log, and a packaged launch that cannot start
also leaves a separate error file behind. Nothing is uploaded anywhere: there is
no telemetry, no analytics, and no third-party crash reporter, so every log
record and every file named here stays on the machine that produced it.

## Log levels

Records go to the `spotm3u` package logger. The level is resolved in this order:

1. `log_level` under `[web]` in `config.toml`
2. the `SPOTM3U_LOG_LEVEL` environment variable
3. `INFO`

`DEBUG` adds per-track stage tracing, candidate accept/reject reasoning in
[matching](matching.md#diagnostics), and the timing records described in
[processing-pipeline.md](processing-pipeline.md#timing-diagnostics). `INFO` is
the default and already includes the timing records. A source run can raise the
level for one command without editing any file:

```bash
SPOTM3U_LOG_LEVEL=DEBUG uv run app
```

Raising the level only makes the same records more verbose. It does not change
what a conversion does.

## Log files

File logging is a mirror of the console output, and it is not always on. A
source run normally has a terminal to print into, so it writes no file unless
you ask for one. A packaged build always writes one, because a windowed Windows
executable has no console to print into. Setting `SPOTM3U_LOG_FILE` requests a
file in any launch mode:

```bash
SPOTM3U_LOG_FILE=~/spotm3u-debug.log uv run app
```

Without that override the file goes to the per-user log directory, which stays
writable and findable even when the application is installed somewhere
protected like `Program Files`:

| Platform | Location |
| --- | --- |
| Windows | `%LOCALAPPDATA%\SpotM3U\logs\spotm3u.log` |
| macOS | `~/Library/Logs/SpotM3U/spotm3u.log` |
| Linux | `$XDG_STATE_HOME/spotm3u/logs/spotm3u.log`, or `~/.local/state/spotm3u/logs/spotm3u.log` when `XDG_STATE_HOME` is unset |

The file rotates at 1 MB and keeps three previous generations beside it as
`spotm3u.log.1` through `spotm3u.log.3`. If the directory cannot be created —
a locked-down profile, a full disk — the log falls back to the platform's
temporary directory rather than being lost. The active path is logged at startup
as `startup log: <path>` on a packaged launch, and the filename resolution lives
in `log_file_path()` in `src/spotm3u/log.py`.

The file handler is attached to the root logger rather than the `spotm3u`
package logger, so Flask's own `app.logger` and Werkzeug's request log land in
the same file. That is what makes an unhandled request error readable from the
log instead of only from a console you may not have.

## Reading a log

Records are formatted as:

```text
2026-01-14 09:31:22 INFO    [spotm3u.jobs] [job=7 track=3rQ2mVwP] resolved from local library
```

The bracketed field is the logger module, and the `job=`/`track=` context follows
it on per-track processing records. A track is identified by its Spotify track
ID when it has one, and otherwise by `title - artists`, so every track has
something searchable to filter on. Grep a job ID to get one conversion end to
end, in the order it happened:

```bash
grep "job=7" ~/Library/Logs/SpotM3U/spotm3u.log
```

Stage-by-stage matching reasoning is covered in [matching](matching.md), and the
pipeline that produces the records in the first place is in
[processing-pipeline.md](processing-pipeline.md).

Credentials, authentication secrets, and private data are never logged. The
pipeline only logs metadata that is already present in the playlist models; see
[security](security.md#credentials).

## Startup failures

A packaged launch configures its file log before anything else runs, so the
reason for a failure survives even when the process dies before any window
appears. The startup sequence is recorded in the same `spotm3u.log` described
above, including the chosen port:

```text
SpotM3U listening on http://127.0.0.1:5001
```

When startup fails, the failure is reported three ways: through the logger, in a
native message box when there is no console, and in a separate
`spotm3u-error.log`. That file is created beside `SpotM3U.exe`, holds a
timestamp and the full traceback, and is appended to rather than overwritten, so
a repeated failure accumulates. It falls back to the platform's temporary
directory when the executable's folder is not writable. The dialog text
deliberately stays free of technical detail because a person who cannot start
the application is not the audience for a traceback; the log file is.

A failed launch exits with a non-zero status, so a wrapper script or CI step can
detect it.

## Failures after startup

There is no crash dump and no crash reporter. An unexpected failure once the
server is up is handled where it happens and written to the log instead:

- A conversion that dies outside the per-track error handling is recorded as
  failed, the unfinished tracks are marked failed so none is left stuck, and the
  traceback goes to the log only. The page shows a message the user can act on,
  never the exception.
- An unhandled request error is logged with its traceback by the Flask handler
  that caught it, which is why the root-logger file described above matters.
- A WebView failure that the shell can recover from — a file picker that will not
  open, a title-bar state that will not sync — is logged at warning or debug
  level and the application keeps running.

So the same log that records normal processing also records the failures, and
`spotm3u-error.log` is specifically about not starting at all. When reporting a
problem, attach the relevant part of the log with secrets removed, as
[CONTRIBUTING.md](../CONTRIBUTING.md) asks; if the application never opened its
window, `spotm3u.log` and `spotm3u-error.log` are the whole story.

## Tests

`tests/unit/utilities/test_logging.py` covers level resolution, handler
idempotency, and the per-track context. `tests/integration/desktop/test_launcher.py`
covers the packaged startup log, the error-log write and its fallback, and the
native dialog.
