"""Persistent processing state: what happened to every track of every conversion.

A conversion used to live only as long as the process that ran it. The job
object in :mod:`spotm3u.jobs` holds the live state the progress pages poll, and
when the app closed that state was gone: there was no way to ask what had been
downloaded last week, why a track failed, or where a file had been written.
Later features (retry, queues, cancellation, background processing) need the same
knowledge to survive a restart, so it is written down here as it happens instead
of being reconstructed from a log afterwards.

The store is the backend's own record, not a view the interface keeps: the
:class:`~spotm3u.jobs.ProcessingJob` writes every transition into it, the API
reads from it, and the React application only observes what it says. Nothing in
the frontend owns state that this module does not hold.

Storage
-------
SQLite (``history.db``) under :func:`spotm3u.runtime.state_dir`, which is the
per-user application data directory and therefore local to the machine. It is a
single embedded file: no server, no service, nothing to install, and the file is
created with its schema on first use so an existing installation keeps working
without a manual step. ``[history] database`` in ``config.toml`` points it
elsewhere, and ``[history] enabled = false`` turns recording off entirely.

State model
-----------
Both tables use the same six states, which is what later work builds on:
``queued``, ``processing``, ``completed``, ``failed``, ``cancelled``, and
``skipped``. The job's finer in-memory stages (:data:`spotm3u.jobs.
TrackProcessingStatus`: searching, downloading, enriching metadata, ...) are not
part of the model: they are recorded as the last known ``stage`` and the state
they belong to is ``processing``. A track the job could not decide about
(``ambiguous``) is stored as ``skipped`` -- no audio was written, so nothing was
processed -- and a track that never started stays ``queued``.

Sizing
------
A history row is deliberately small: identity, the outcome, a path, a reason, and
timestamps. Tags, artwork, lyrics, and the response bodies of the sources are
never copied in, and free text (a reason, an error) is truncated, so the file
grows with the number of conversions rather than with their size. Old runs are
pruned to ``[history] max_runs`` once a new run begins, which also keeps a
long-lived library's history from becoming a log dump.

Failure
-------
Recording history is never worth losing a download over: every write is
best-effort, a database that cannot be opened disables the store instead of
raising, and a write that fails is logged and dropped. The store reports
``available`` so the API can say so plainly rather than answering with an empty
history that looks like "nothing was ever downloaded".
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Track
from .runtime import state_dir

logger = logging.getLogger(__name__)

HISTORY_FILENAME = "history.db"

# How many finished runs are kept. Older ones are pruned when a new run begins,
# so the file stays a usable size no matter how long the app is used.
DEFAULT_MAX_RUNS = 200

# Ceilings for the free text a reason or an error can contribute. The messages
# come from the resolvers and the downloader, not from a user's keyboard, but a
# source that answers with a page of HTML must not end up in the history.
MAX_TEXT_LENGTH = 500

# The states every history row carries. ``cancelled`` and ``skipped`` are part of
# the model from the start so cancellation, recovery, and queues can be built on
# it without another migration.
RUN_STATUSES: tuple[str, ...] = (
    "queued",
    "processing",
    "completed",
    "failed",
    "cancelled",
    "skipped",
)
TRACK_STATUSES: tuple[str, ...] = RUN_STATUSES

# How a job's in-memory track status is stored. The stages a track passes
# through on its way to a decision are all ``processing``; the stage itself is
# kept alongside so the history can say where a track was when it was last
# written, without every stage becoming a state of its own.
_PERSISTED_TRACK_STATUS: dict[str, str] = {
    "queued": "queued",
    "resolving-local": "processing",
    "searching": "processing",
    "searched": "processing",
    "validating-source": "processing",
    "downloading": "processing",
    "validating-audio": "processing",
    "enriching-metadata": "processing",
    "complete": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "ambiguous": "skipped",
    "skipped": "skipped",
}

# A job's own status, which names the same lifecycle with ``running`` for
# ``processing``.
_PERSISTED_RUN_STATUS: dict[str, str] = {
    "queued": "queued",
    "active": "processing",
    "running": "processing",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}

# The sort orders the history list offers. Newest first is the useful default:
# the recent conversions are the ones being looked for.
HISTORY_ORDERS: tuple[str, ...] = ("recent", "oldest", "name")

# A run left mid-flight by a closed app is reported as cancelled: the app cannot
# know whether the download got far enough to finish, and claiming a conversion
# is still running forever would be a worse lie than saying it was interrupted.
INTERRUPTED_ERROR = "The app was closed before this conversion finished."

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key TEXT NOT NULL UNIQUE,
    job_id TEXT NOT NULL DEFAULT '',
    playlist_id TEXT NOT NULL DEFAULT '',
    playlist_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    fast_mode INTEGER NOT NULL DEFAULT 0,
    total_tracks INTEGER NOT NULL DEFAULT 0,
    output_dir TEXT NOT NULL DEFAULT '',
    m3u_path TEXT,
    error TEXT NOT NULL DEFAULT '',
    started_at INTEGER NOT NULL,
    finished_at INTEGER
);

CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_ref INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    artists TEXT NOT NULL DEFAULT '',
    album TEXT,
    spotify_id TEXT,
    status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT '',
    resolution TEXT NOT NULL DEFAULT '',
    source_url TEXT,
    output_path TEXT,
    reason TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    cancelled INTEGER NOT NULL DEFAULT 0,
    queued_at INTEGER NOT NULL,
    started_at INTEGER,
    finished_at INTEGER,
    UNIQUE (run_ref, position)
);

-- One row per attempt at a track. ``tracks`` holds the attempt that is current
-- so every count, filter and listing reads one table; this keeps what the
-- earlier attempts did, which is the whole point of retrying: a failed attempt
-- must still be readable after the next one replaces it.
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_ref INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT '',
    resolution TEXT NOT NULL DEFAULT '',
    source_url TEXT,
    output_path TEXT,
    reason TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    queued_at INTEGER NOT NULL,
    started_at INTEGER,
    finished_at INTEGER,
    UNIQUE (track_ref, attempt)
);

CREATE INDEX IF NOT EXISTS tracks_run_status ON tracks (run_ref, status);
CREATE INDEX IF NOT EXISTS runs_status ON runs (status);
CREATE INDEX IF NOT EXISTS attempts_track ON attempts (track_ref, attempt);
"""


def persisted_track_status(status: str) -> str:
    """The stored state a job's in-memory track status belongs to.

    An unknown status is stored as ``processing`` rather than refused: a stage
    added by a later version must still be recorded, and the stage text beside it
    keeps the detail.
    """
    return _PERSISTED_TRACK_STATUS.get(status, "processing")


def persisted_run_status(status: str) -> str:
    """The stored state a job's own status belongs to."""
    return _PERSISTED_RUN_STATUS.get(status, "processing")


def history_db_path() -> Path:
    """The file the processing history is stored in."""
    return state_dir() / HISTORY_FILENAME


def _now_ms() -> int:
    """The current time in the epoch-milliseconds the API reports."""
    return int(time.time() * 1000)


def _text(value: object, limit: int = MAX_TEXT_LENGTH) -> str:
    """A stored string: ``None`` becomes empty and long text is truncated."""
    if value is None:
        return ""
    text = str(value).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# One queued track row, re-used when a conversion starts and when a retry puts
# its tracks back. ``ON CONFLICT`` keeps the identity columns of a track that is
# already stored, so re-running a conversion does not lose what it was.
_QUEUE_TRACK_SQL = (
    "INSERT INTO tracks (run_ref, position, title, artists, album, spotify_id, status, "
    "stage, queued_at) VALUES (?, ?, ?, ?, ?, ?, 'queued', 'queued', ?) "
    "ON CONFLICT (run_ref, position) DO UPDATE SET status = 'queued', stage = 'queued', "
    "error = '', reason = '', resolution = '', output_path = NULL, source_url = NULL, "
    "finished_at = NULL, started_at = NULL, queued_at = excluded.queued_at"
)


def _queue_parameters(run_ref: int, position: int, track: Track, now: int) -> tuple[Any, ...]:
    """The values for one queued track row."""
    return (
        run_ref,
        position,
        _text(track.title),
        _text(", ".join(track.artists)),
        _text(track.album) or None,
        _text(track.spotify_id) or None,
        now,
    )


class HistoryUnavailableError(RuntimeError):
    """The processing history is switched off or could not be opened."""


@dataclass(frozen=True)
class RunIdentity:
    """What identifies one conversion, whoever started it."""

    job_id: str
    playlist_id: str
    playlist_name: str
    total_tracks: int
    output_dir: str = ""
    fast_mode: bool = False

    @property
    def key(self) -> str:
        """The stable identity of the run: one playlist within one upload."""
        return f"{self.job_id}:{self.playlist_id}"


class HistoryStore:
    """The backend's record of processing state, kept in a local SQLite file.

    One instance per application, created by :func:`spotm3u.app.create_app` and
    handed to the jobs it starts. Every method is safe to call from a job's
    worker threads and returns quietly when the database is unavailable, so a
    conversion never fails because its history could not be written.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        enabled: bool = True,
        max_runs: int = DEFAULT_MAX_RUNS,
    ) -> None:
        self.path = Path(path) if path is not None else history_db_path()
        self.max_runs = max(1, int(max_runs))
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None
        self._failure: str = ""
        if enabled:
            self._open()

    # -- lifecycle ---------------------------------------------------------

    @property
    def available(self) -> bool:
        """Whether the history can be read and written right now."""
        return self._connection is not None

    @property
    def failure(self) -> str:
        """Why the store is unavailable, for the log and the API's own answer."""
        return self._failure

    def _open(self) -> None:
        """Create the file and its schema, or disable the store on failure."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.path,
                # The jobs write from their own worker threads, so the one
                # connection is shared under ``self._lock``.
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            # WAL keeps a reader (the history page) from blocking the writers
            # (the job), and the busy timeout absorbs the brief overlap anyway.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.executescript(_SCHEMA)
            self._backfill_attempts(connection)
            # The backfill writes, so it has to be committed here: reconcile()
            # below returns without committing when no run is in flight, which
            # would leave this transaction open and lock the file.
            connection.commit()
        except (OSError, sqlite3.Error) as exc:
            self._failure = str(exc)
            logger.warning("Processing history is unavailable at %s: %s", self.path, exc)
            return
        self._connection = connection
        self.reconcile()

    def close(self) -> None:
        """Close the database file, for a clean shutdown or a test."""
        with self._lock:
            connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    @staticmethod
    def _open_first_attempt(
        connection: sqlite3.Connection,
        run_ref: int,
        position: int,
        now: int,
    ) -> None:
        """Give a newly queued track its first attempt row.

        A run that already stored the track has its attempts; only a track seen
        for the first time needs one opened, which is why this checks before
        inserting rather than relying on the unique index to fail.
        """
        track = connection.execute(
            "SELECT id FROM tracks WHERE run_ref = ? AND position = ?", (run_ref, position)
        ).fetchone()
        if track is None:
            return
        track_ref = int(track["id"])
        connection.execute(
            "INSERT INTO attempts (track_ref, attempt, status, stage, queued_at) "
            "SELECT ?, 1, 'queued', 'queued', ? WHERE NOT EXISTS ("
            "SELECT 1 FROM attempts WHERE track_ref = ?)",
            (track_ref, now, track_ref),
        )

    @staticmethod
    def _backfill_attempts(connection: sqlite3.Connection) -> None:
        """Give every already stored track a first attempt row.

        The attempts table arrived after the tracks table, so a history written
        by an earlier version has rows that were retried without their earlier
        attempts being kept. Those attempts are simply gone, and this records
        what is still known: the current state, as attempt ``retry_count + 1``.
        A track retried three times therefore lists one attempt numbered 4, which
        is why the API reports the retry count separately from the attempt list.
        """
        connection.execute(
            "INSERT INTO attempts (track_ref, attempt, status, stage, resolution, source_url, "
            "output_path, reason, error, queued_at, started_at, finished_at) "
            "SELECT id, retry_count + 1, status, stage, resolution, source_url, output_path, "
            "reason, error, queued_at, started_at, finished_at FROM tracks "
            "WHERE id NOT IN (SELECT track_ref FROM attempts)"
        )

    def reconcile(self) -> None:
        """Close out the runs an earlier session left in flight.

        Nothing is running when the application starts, so a run still marked
        ``queued`` or ``processing`` belongs to a session that ended. It is
        recorded as cancelled with the reason, and the tracks that never reached
        a decision go with it, which is what makes the history honest after a
        restart instead of showing a conversion that will never advance.
        """
        now = _now_ms()
        with self._lock:
            connection = self._connection
            if connection is None:
                return
            try:
                in_flight = connection.execute(
                    "SELECT id FROM runs WHERE status IN ('queued', 'processing')"
                ).fetchall()
                if not in_flight:
                    return
                placeholders = ", ".join("?" * len(in_flight))
                references = [int(row["id"]) for row in in_flight]
                mark_runs = (
                    "UPDATE runs SET status = 'cancelled', error = ?, "
                    f"finished_at = COALESCE(finished_at, ?) WHERE id IN ({placeholders})"
                )
                mark_tracks = (
                    "UPDATE tracks SET status = 'cancelled', cancelled = 1, error = ?, "
                    "finished_at = COALESCE(finished_at, ?) "
                    f"WHERE run_ref IN ({placeholders}) AND status IN ('queued', 'processing')"
                )
                connection.execute(mark_runs, (INTERRUPTED_ERROR, now, *references))
                connection.execute(mark_tracks, (INTERRUPTED_ERROR, now, *references))
                self._cancel_open_attempts(connection, now, INTERRUPTED_ERROR)
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                logger.warning("Processing history could not be closed out: %s", exc)

    @staticmethod
    def _cancel_open_attempts(
        connection: sqlite3.Connection,
        now: int,
        error: str,
        track_refs: Sequence[int] | None = None,
    ) -> None:
        """Finish the attempt rows that never reached a decision.

        An attempt is written when a retry starts, so one without a
        ``finished_at`` is an attempt that was abandoned rather than one that
        completed. Restricting the sweep to ``track_refs`` keeps a retry from
        closing the attempts of the tracks it did not pick up.
        """
        sql = (
            "UPDATE attempts SET status = 'cancelled', error = ?, finished_at = ? "
            "WHERE finished_at IS NULL"
        )
        if track_refs is None:
            connection.execute(sql, (error, now))
            return
        placeholders = ", ".join("?" * len(track_refs))
        connection.execute(
            f"{sql} AND track_ref IN ({placeholders})",
            (error, now, *track_refs),
        )

    # -- writes ------------------------------------------------------------

    def _write_many(self, what: str, statements: Sequence[tuple[str, Sequence[Any]]]) -> bool:
        """Run several statements as one transaction, failing as a unit."""
        with self._lock:
            connection = self._connection
            if connection is None:
                return False
            try:
                for sql, parameters in statements:
                    connection.execute(sql, tuple(parameters))
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                logger.warning("Processing history could not be written (%s): %s", what, exc)
                return False
        return True

    def _write(self, what: str, sql: str, parameters: Sequence[Any] = ()) -> bool:
        """Run one write, swallowing a failure so a conversion is never lost."""
        return self._write_many(what, ((sql, parameters),))

    def _query(self, sql: str, parameters: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            connection = self._connection
            if connection is None:
                return []
            try:
                return list(connection.execute(sql, tuple(parameters)))
            except sqlite3.Error as exc:
                logger.warning("Processing history could not be read: %s", exc)
        return []

    def begin_run(
        self, run: RunIdentity, tracks: Sequence[Track], *, status: str = "processing"
    ) -> int | None:
        """Record a conversion that is starting, with one row per track.

        ``status`` is the state the run is being recorded in, which is not
        always ``processing``: work that has been queued but has not started is
        written down as ``queued`` so it survives the interface being closed.

        A retry re-uses the run it belongs to (the same job and playlist), so
        the history keeps one entry per conversion rather than one per attempt.
        Returns the run's reference, or ``None`` when nothing was written.
        """
        now = _now_ms()
        with self._lock:
            connection = self._connection
            if connection is None:
                return None
            try:
                existing = connection.execute(
                    "SELECT id FROM runs WHERE run_key = ?", (run.key,)
                ).fetchone()
                if existing is not None:
                    run_ref = int(existing["id"])
                    connection.execute(
                        "UPDATE runs SET status = ?, error = '', finished_at = NULL, "
                        "playlist_name = ?, total_tracks = ?, output_dir = ?, fast_mode = ? "
                        "WHERE id = ?",
                        (
                            status,
                            _text(run.playlist_name),
                            run.total_tracks,
                            run.output_dir,
                            int(run.fast_mode),
                            run_ref,
                        ),
                    )
                else:
                    cursor = connection.execute(
                        "INSERT INTO runs (run_key, job_id, playlist_id, playlist_name, status, "
                        "fast_mode, total_tracks, output_dir, started_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            run.key,
                            _text(run.job_id),
                            _text(run.playlist_id),
                            _text(run.playlist_name),
                            status,
                            int(run.fast_mode),
                            run.total_tracks,
                            run.output_dir,
                            now,
                        ),
                    )
                    run_ref = int(cursor.lastrowid or 0)
                for position, track in enumerate(tracks):
                    connection.execute(
                        _QUEUE_TRACK_SQL, _queue_parameters(run_ref, position, track, now)
                    )
                    self._open_first_attempt(connection, run_ref, position, now)
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                logger.warning("Processing history could not record a run: %s", exc)
                return None
        self._prune()
        return run_ref

    def set_run_status(self, run_ref: int, status: str) -> None:
        """Move a run to ``status`` without touching the tracks it already holds.

        Used by the queue: work that is waiting again after a retry already has
        its track rows, and re-recording them would throw away the outcomes of
        the tracks that are finished.
        """
        self._write(
            "recording a run status",
            "UPDATE runs SET status = ?, finished_at = NULL WHERE id = ?",
            (status, run_ref),
        )

    def cancel_run(self, run_ref: int, *, reason: str = "") -> None:
        """Record a run and every track it has not decided as ``cancelled``.

        Cancellation is not failure: a cancelled run is work the user stopped
        choosing to do, not work that went wrong, and the history has to be able
        to say which of the two it was.
        """
        now = _now_ms()
        text = _text(reason)
        self._write(
            "cancelling a run",
            "UPDATE runs SET status = 'cancelled', error = ?, finished_at = ? "
            "WHERE id = ? AND status IN ('queued', 'processing')",
            (text, now, run_ref),
        )
        self._write(
            "cancelling a run's tracks",
            "UPDATE tracks SET status = 'cancelled', stage = 'cancelled', reason = ?, "
            "finished_at = ? WHERE run_ref = ? AND status IN ('queued', 'processing')",
            (text, now, run_ref),
        )
        self._write(
            "cancelling a run's attempts",
            "UPDATE attempts SET status = 'cancelled', reason = ?, finished_at = ? "
            "WHERE track_ref IN (SELECT id FROM tracks WHERE run_ref = ?) "
            "AND status IN ('queued', 'processing')",
            (text, now, run_ref),
        )

    def set_track_stage(self, run_ref: int, position: int, *, status: str, stage: str) -> None:
        """Record that a track moved into ``status`` for ``stage``."""
        now = _now_ms()
        self._write(
            "recording a track stage",
            "UPDATE tracks SET status = ?, stage = ?, started_at = COALESCE(started_at, ?) "
            "WHERE run_ref = ? AND position = ?",
            (status, _text(stage, 64), now, run_ref, position),
        )

    def finish_track(
        self,
        run_ref: int,
        position: int,
        *,
        status: str,
        stage: str = "",
        resolution: str = "",
        source_url: str | None = None,
        output_path: str | Path | None = None,
        reason: str = "",
        error: str = "",
    ) -> None:
        """Record a track's final state, with what produced it.

        The attempt that was running is closed with the same values, so a later
        retry that replaces the track row leaves this outcome readable.
        """
        now = _now_ms()
        self._write_many(
            "recording a finished track",
            (
                (
                    "UPDATE tracks SET status = ?, stage = ?, resolution = ?, source_url = ?, "
                    "output_path = ?, reason = ?, error = ?, finished_at = ? "
                    "WHERE run_ref = ? AND position = ?",
                    (
                        status,
                        _text(stage, 64) or _text(resolution, 64),
                        _text(resolution, 64),
                        _text(source_url) or None,
                        _text(output_path) or None,
                        _text(reason),
                        _text(error),
                        now,
                        run_ref,
                        position,
                    ),
                ),
                (
                    "UPDATE attempts SET status = ?, stage = ?, resolution = ?, source_url = ?, "
                    "output_path = ?, reason = ?, error = ?, started_at = COALESCE(started_at, ?), "
                    "finished_at = ? WHERE id = (SELECT id FROM attempts WHERE track_ref = "
                    "(SELECT id FROM tracks WHERE run_ref = ? AND position = ?) "
                    "ORDER BY attempt DESC LIMIT 1)",
                    (
                        status,
                        _text(stage, 64) or _text(resolution, 64),
                        _text(resolution, 64),
                        _text(source_url) or None,
                        _text(output_path) or None,
                        _text(reason),
                        _text(error),
                        now,
                        now,
                        run_ref,
                        position,
                    ),
                ),
            ),
        )

    def requeue_tracks(self, run_ref: int, positions: Sequence[int]) -> None:
        """Put the retried tracks back to ``queued`` and start a new attempt.

        The finished rows of the tracks that are not being retried are left
        alone, so a retry costs only the tracks it picked up. A retried track
        keeps the reason and source of the attempt that failed until the new
        one lands -- the queued row has to say why it is queued -- and the
        attempt it replaces is closed as cancelled rather than dropped, so
        nothing about the earlier failure is lost.
        """
        now = _now_ms()
        with self._lock:
            connection = self._connection
            if connection is None:
                return
            try:
                connection.execute(
                    "UPDATE runs SET status = 'processing', error = '', finished_at = NULL "
                    "WHERE id = ?",
                    (run_ref,),
                )
                for position in positions:
                    connection.execute(
                        "UPDATE tracks SET status = 'queued', stage = 'queued', "
                        "output_path = NULL, retry_count = retry_count + 1, queued_at = ?, "
                        "started_at = NULL, finished_at = NULL "
                        "WHERE run_ref = ? AND position = ?",
                        (now, run_ref, position),
                    )
                    track = connection.execute(
                        "SELECT id FROM tracks WHERE run_ref = ? AND position = ?",
                        (run_ref, position),
                    ).fetchone()
                    if track is None:
                        continue
                    track_ref = int(track["id"])
                    self._cancel_open_attempts(connection, now, INTERRUPTED_ERROR, (track_ref,))
                    connection.execute(
                        "INSERT INTO attempts (track_ref, attempt, status, stage, queued_at) "
                        "SELECT id, retry_count + 1, 'queued', 'queued', ? FROM tracks "
                        "WHERE id = ?",
                        (now, track_ref),
                    )
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                logger.warning("Processing history could not record the retry: %s", exc)

    def finish_run(
        self,
        run_ref: int,
        *,
        status: str,
        m3u_path: str | Path | None = None,
        error: str = "",
    ) -> None:
        """Record that the run reached a final state."""
        self._write(
            "recording a finished run",
            "UPDATE runs SET status = ?, m3u_path = ?, error = ?, finished_at = ? WHERE id = ?",
            (
                status,
                _text(m3u_path) or None,
                _text(error),
                _now_ms(),
                run_ref,
            ),
        )

    def _prune(self) -> None:
        """Drop the oldest finished runs beyond ``max_runs``.

        Runs that are still in flight are never pruned, so a conversion running
        when the limit is reached keeps its rows.
        """
        keep = self.max_runs
        self._write(
            "pruning old runs",
            "DELETE FROM runs WHERE status IN ('completed', 'failed', 'cancelled', 'skipped') "
            "AND id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT ?)",
            (keep,),
        )

    # -- reads -------------------------------------------------------------

    @staticmethod
    def _filters(*, status: str | None, query: str | None) -> tuple[str, list[Any]]:
        """The WHERE clause and parameters for a filtered run list."""
        clauses: list[str] = []
        parameters: list[Any] = []
        if status:
            # A state selects the runs in it *and* the runs holding a track in
            # it: a finished conversion can still be what the user is looking
            # for when a few of its tracks were skipped or failed, and a run that
            # is being retried is in flight with only some of its tracks done.
            clauses.append(
                "(r.status = ? OR EXISTS ("
                "SELECT 1 FROM tracks t WHERE t.run_ref = r.id AND t.status = ?))"
            )
            parameters += [status, status]
        if query:
            # Match the playlist as well as its tracks, so "road trip" finds the
            # conversion that held those songs and not only one titled so.
            clauses.append(
                "(r.playlist_name LIKE ? COLLATE NOCASE OR EXISTS ("
                "SELECT 1 FROM tracks t WHERE t.run_ref = r.id AND ("
                "t.title LIKE ? COLLATE NOCASE OR t.artists LIKE ? COLLATE NOCASE)))"
            )
            pattern = f"%{query}%"
            parameters += [pattern, pattern, pattern]
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, parameters

    def list_runs(
        self,
        *,
        status: str | None = None,
        query: str | None = None,
        order: str = "recent",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        """The stored runs, newest first by default."""
        where, parameters = self._filters(status=status, query=query)
        sorting = {
            "recent": "r.id DESC",
            "oldest": "r.id ASC",
            "name": "r.playlist_name COLLATE NOCASE ASC, r.id DESC",
        }.get(order, "r.id DESC")
        rows = self._query(
            f"SELECT r.* FROM runs r{where} ORDER BY {sorting} LIMIT ? OFFSET ?",
            [*parameters, max(1, int(limit)), max(0, int(offset))],
        )
        return [self._run_payload(row) for row in rows]

    def count_runs(self, *, status: str | None = None, query: str | None = None) -> int:
        """How many runs the same filter matches, for the page's total."""
        where, parameters = self._filters(status=status, query=query)
        rows = self._query(f"SELECT COUNT(*) AS total FROM runs r{where}", parameters)
        return int(rows[0]["total"]) if rows else 0

    def get_run(self, run_ref: int) -> dict[str, object] | None:
        """One run with its tracks in playlist order, or ``None``.

        Each track carries its attempts, so the interface can show what an
        earlier retry did without a second request.
        """
        rows = self._query("SELECT * FROM runs WHERE id = ?", (int(run_ref),))
        if not rows:
            return None
        run = self._run_payload(rows[0])
        tracks: list[dict[str, object]] = []
        for row in self._query(
            "SELECT * FROM tracks WHERE run_ref = ? ORDER BY position", (int(run_ref),)
        ):
            track = self._track_payload(row)
            track["attempts"] = self._attempts_for(int(row["id"]))
            tracks.append(track)
        run["tracks"] = tracks
        return run

    def _attempts_for(self, track_ref: int) -> list[dict[str, object]]:
        """Every attempt at one track, oldest first, as the API reports them."""
        return [
            self._attempt_payload(row)
            for row in self._query(
                "SELECT * FROM attempts WHERE track_ref = ? ORDER BY attempt", (track_ref,)
            )
        ]

    def _run_payload(self, row: sqlite3.Row) -> dict[str, object]:
        """One run as the API reports it, with its per-state track counts.

        The counts are counted from the track rows rather than stored, so they
        cannot drift from the state that is actually recorded, and a run that was
        interrupted keeps reporting the tracks that never finished.
        """
        counts = {status: 0 for status in TRACK_STATUSES}
        for entry in self._query(
            "SELECT status, COUNT(*) AS total FROM tracks WHERE run_ref = ? GROUP BY status",
            (int(row["id"]),),
        ):
            counts[str(entry["status"])] = int(entry["total"])
        return {
            "id": int(row["id"]),
            "job_id": str(row["job_id"]),
            "playlist_id": str(row["playlist_id"]),
            "playlist_name": str(row["playlist_name"]),
            "status": str(row["status"]),
            "fast_mode": bool(row["fast_mode"]),
            "total_tracks": int(row["total_tracks"]),
            "output_dir": str(row["output_dir"]),
            "m3u_path": row["m3u_path"],
            "error": str(row["error"]),
            "started_at": int(row["started_at"]),
            "finished_at": int(row["finished_at"]) if row["finished_at"] is not None else None,
            "counts": counts,
        }

    @staticmethod
    def _track_payload(row: sqlite3.Row) -> dict[str, object]:
        """One track as the API reports it."""
        return {
            "position": int(row["position"]),
            "title": str(row["title"]),
            "artists": str(row["artists"]),
            "album": row["album"],
            "spotify_id": row["spotify_id"],
            "status": str(row["status"]),
            "stage": str(row["stage"]),
            "resolution": str(row["resolution"]),
            "source_url": row["source_url"],
            "output_path": row["output_path"],
            "reason": str(row["reason"]),
            "error": str(row["error"]),
            "retry_count": int(row["retry_count"]),
            "cancelled": bool(row["cancelled"]),
            "queued_at": int(row["queued_at"]),
            "started_at": int(row["started_at"]) if row["started_at"] is not None else None,
            "finished_at": int(row["finished_at"]) if row["finished_at"] is not None else None,
        }

    @staticmethod
    def _attempt_payload(row: sqlite3.Row) -> dict[str, object]:
        """One recorded attempt as the API reports it."""
        return {
            "attempt": int(row["attempt"]),
            "status": str(row["status"]),
            "stage": str(row["stage"]),
            "resolution": str(row["resolution"]),
            "source_url": row["source_url"],
            "output_path": row["output_path"],
            "reason": str(row["reason"]),
            "error": str(row["error"]),
            "queued_at": int(row["queued_at"]),
            "started_at": int(row["started_at"]) if row["started_at"] is not None else None,
            "finished_at": int(row["finished_at"]) if row["finished_at"] is not None else None,
        }


__all__ = [
    "DEFAULT_MAX_RUNS",
    "HISTORY_FILENAME",
    "HISTORY_ORDERS",
    "INTERRUPTED_ERROR",
    "MAX_TEXT_LENGTH",
    "RUN_STATUSES",
    "TRACK_STATUSES",
    "HistoryStore",
    "HistoryUnavailableError",
    "RunIdentity",
    "history_db_path",
    "persisted_run_status",
    "persisted_track_status",
]
