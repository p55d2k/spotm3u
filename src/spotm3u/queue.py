"""The bounded download queue that conversions wait in.

A conversion is a playlist's worth of tracks, and a playlist's tracks are
already processed in parallel. Several playlists at once is a different thing:
each one opens its own searches, downloads and connections, so the only honest
way to cap the load is to cap how many conversions are in flight and let the
rest wait their turn.

This module is that cap. It is deliberately a separate object from
:class:`~spotm3u.jobs.JobManager` rather than a field on it: the manager knows
which conversions exist, while this knows which of them are allowed to be
running, and keeping them apart is what makes the limit testable on its own.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from spotm3u.jobs import JobNotCancellableError, JobStartError

if TYPE_CHECKING:
    from spotm3u.jobs import ProcessingJob

logger = logging.getLogger(__name__)

QueueState = Literal["queued", "active", "completed", "failed", "cancelled"]

QUEUE_STATES: tuple[QueueState, ...] = ("queued", "active", "completed", "failed", "cancelled")

# Two conversions at once is the default because it is where a run is still
# comfortably useful: each one already parallelises its own tracks, so a single
# conversion is usually not using the whole machine, and a third one mostly
# buys contention. The ceiling exists to protect the app and the network, not to
# be as low as possible.
DEFAULT_MAX_ACTIVE_PLAYLISTS = 2

# A conversion dropped before it starts is not a failure, and the reason it was
# dropped belongs to the queue rather than the job, so the job's own wording is
# about stopping work once it has begun.
QUEUE_JOB_CANCELLED = "Cancelled while waiting to run."


class QueueError(RuntimeError):
    """The queue refused a conversion."""


class QueueClosedError(QueueError):
    """The queue is shut down and will not take new work."""


class QueueJobConflictError(QueueError):
    """The conversion is already in the queue in a state that forbids this."""


class UnknownQueueEntryError(QueueError):
    """The conversion was never queued, so the queue has nothing to do with it."""


class QueueJobNotCancellableError(QueueError):
    """The conversion has already started, so it cannot simply be dropped.

    ``state`` says what it is doing instead, so a caller can tell the user
    something useful.
    """

    def __init__(self, message: str, *, state: str = "active") -> None:
        super().__init__(message)
        self.state = state


@dataclass(frozen=True)
class QueueEntry:
    """Where one conversion is in the queue, as the interface sees it."""

    job_id: str
    playlist_id: str
    playlist_name: str
    state: QueueState
    total_tracks: int
    position: int | None
    queued_at: float
    started_at: float | None
    finished_at: float | None
    error: str = ""

    @property
    def settled(self) -> bool:
        """True once this conversion will not change state on its own again."""
        return self.state in {"completed", "failed", "cancelled"}

    def as_dict(self) -> dict[str, object]:
        """The JSON shape the interface polls for."""
        return {
            "job_id": self.job_id,
            "playlist_id": self.playlist_id,
            "playlist_name": self.playlist_name,
            "state": self.state,
            "total_tracks": self.total_tracks,
            "position": self.position,
            "queued_at": int(self.queued_at * 1000),
            "started_at": int(self.started_at * 1000) if self.started_at else None,
            "finished_at": int(self.finished_at * 1000) if self.finished_at else None,
            "error": self.error,
        }


@dataclass
class _Slot:
    """The queue's own record of one conversion.

    The conversion's progress lives on the job; this only says whether the queue
    has let it run yet, so the queue can answer for work that has not started
    and for work that has already finished.
    """

    job: ProcessingJob
    state: QueueState = "queued"
    queued_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.job.job_id, self.job.playlist_id)

    def entry(self, position: int | None) -> QueueEntry:
        return QueueEntry(
            job_id=self.job.job_id,
            playlist_id=self.job.playlist_id,
            playlist_name=self.job.playlist_name,
            state=self.state,
            total_tracks=len(self.job.tracks),
            position=position,
            queued_at=self.queued_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            error=self.job.error or "",
        )


class ConversionQueue:
    """Runs a bounded number of conversions at a time, in the order given.

    A conversion is accepted whether or not it can start, which is the whole
    point: a caller never has to know how many are already running. Each one is
    started by its own thread, and that thread is the queue's slot: the number
    of them is exactly the number of conversions allowed to run, so the limit is
    the shape of the queue rather than a check somewhere inside a job.
    """

    def __init__(self, *, max_active: int = DEFAULT_MAX_ACTIVE_PLAYLISTS) -> None:
        self._max_active = max(1, int(max_active))
        # One lock for the queue's own bookkeeping. A condition rather than a
        # plain lock because "tell me when the queue is empty" is a question the
        # shutdown path and the tests both need answered without polling.
        self._condition = threading.Condition(threading.RLock())
        # Waiting conversions, oldest first. Cancelling one only has to drop it
        # from here: it has no thread yet, so there is nothing to stop.
        self._waiting: deque[_Slot] = deque()
        self._slots: dict[tuple[str, str], _Slot] = {}
        # The conversions holding one of the slots, and the threads holding them.
        self._active: set[tuple[str, str]] = set()
        self._closed = False

    @property
    def max_active(self) -> int:
        """How many conversions may run at once."""
        return self._max_active

    @property
    def active_count(self) -> int:
        with self._condition:
            return len(self._active)

    @property
    def waiting_count(self) -> int:
        with self._condition:
            return len(self._waiting)

    # -- waiting -----------------------------------------------------------

    def submit(self, job: ProcessingJob) -> QueueEntry:
        """Put a conversion at the back of the queue and return where it is.

        The conversion is recorded as waiting before this returns, so work that
        is only queued is still written down, and it takes a slot if one is free.
        """
        with self._condition:
            self._reject_if_closed()
            slot = self._slot_for(job)
            slot.state = "queued"
            slot.queued_at = time.time()
            slot.started_at = None
            slot.finished_at = None
            self._waiting.append(slot)
            starting = self._take_available()
        # The run is written down outside the lock: the history is a database,
        # and holding the queue's lock across a write would let a slow disk
        # delay every other conversion.
        job.reserve()
        with self._condition:
            entry = slot.entry(self._position(slot))
            self._condition.notify_all()
        self._launch(starting)
        return entry

    def submit_retry(
        self, job: ProcessingJob, indices: Sequence[int] | None = None
    ) -> tuple[int, ...]:
        """Queue a retry behind the conversions already waiting.

        A retry is work like any other, so it takes a slot instead of running
        immediately; that is what stops a batch of retries from ignoring the
        limit. Returns the track indices that were queued, which is empty when
        there was nothing left to retry.
        """
        with self._condition:
            self._reject_if_closed()
            if (job.job_id, job.playlist_id) in self._active:
                raise QueueJobConflictError("conversion is already running")
            known = (job.job_id, job.playlist_id) in self._slots
        # The reset happens outside the lock: it is the job's own bookkeeping
        # and may write to the history, which is not the queue's business.
        selected = job.prepare_retry(indices)
        if not selected:
            return selected
        if not known:
            self.submit(job)
            return selected
        with self._condition:
            slot = self._slots[(job.job_id, job.playlist_id)]
            slot.state = "queued"
            slot.queued_at = time.time()
            slot.started_at = None
            slot.finished_at = None
            self._waiting.append(slot)
            starting = self._take_available()
            self._condition.notify_all()
        self._launch(starting)
        return selected

    def cancel(self, job_id: str, playlist_id: str) -> QueueEntry:
        """Drop a conversion that has not started and record it as cancelled.

        Only waiting work can be cancelled this way. A conversion that has begun
        is stopped by pausing or cancelling the run, which is a different
        operation and is deliberately not reachable from here.
        """
        with self._condition:
            slot = self._slots.get((job_id, playlist_id))
            if slot is None:
                raise UnknownQueueEntryError("conversion is not in the queue")
            if slot.state != "queued":
                raise QueueJobNotCancellableError(
                    "conversion has already started", state=slot.state
                )
            self._waiting.remove(slot)
            slot.state = "cancelled"
            slot.finished_at = time.time()
            starting = self._take_available()
        try:
            slot.job.cancel_before_start(QUEUE_JOB_CANCELLED)
        except JobNotCancellableError:  # pragma: no cover - the queue just checked
            logger.warning("conversion %s started while being cancelled", slot.key)
        with self._condition:
            entry = slot.entry(None)
            self._condition.notify_all()
        self._launch(starting)
        return entry

    # -- reading -----------------------------------------------------------

    def entry(self, job_id: str, playlist_id: str) -> QueueEntry | None:
        """Where one conversion is, or ``None`` if the queue never had it."""
        with self._condition:
            slot = self._slots.get((job_id, playlist_id))
            return None if slot is None else slot.entry(self._position(slot))

    def state_of(self, job: ProcessingJob) -> QueueState | None:
        """The queue's state for a conversion, or ``None`` if it never queued."""
        with self._condition:
            slot = self._slots.get((job.job_id, job.playlist_id))
            return None if slot is None else slot.state

    def count_in(self, state: QueueState) -> int:
        """How many conversions the queue currently holds in ``state``."""
        with self._condition:
            return sum(1 for slot in self._slots.values() if slot.state == state)

    def snapshot(self) -> dict[str, object]:
        """The whole queue, for the interface.

        The counts are what a header needs ("2 of 5 running, 3 waiting") and the
        entries are what a list needs, in the order the user gave them.
        """
        with self._condition:
            entries = [
                slot.entry(self._position(slot))
                for slot in sorted(self._slots.values(), key=lambda item: item.queued_at)
            ]
        counts = dict.fromkeys(QUEUE_STATES, 0)
        for entry in entries:
            counts[entry.state] += 1
        return {
            "max_active": self._max_active,
            "active": counts["active"],
            "waiting": counts["queued"],
            "completed": counts["completed"],
            "failed": counts["failed"],
            "cancelled": counts["cancelled"],
            "total": len(entries),
            "entries": [entry.as_dict() for entry in entries],
        }

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Stop handing out slots, without stopping anything already running.

        The queue is a scheduling decision, not a resource the conversions need
        in order to reach their own outcome, so shutting it down refuses to start
        anything new and leaves the work in flight to finish.
        """
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def wait_for_idle(self, timeout: float | None = None) -> bool:
        """Block until nothing is running or waiting. Returns whether it is idle.

        Used by shutdown and by tests, both of which need to know the queue has
        settled rather than assume it.
        """
        with self._condition:
            return self._condition.wait_for(lambda: not self._active and not self._waiting, timeout)

    # -- internals ---------------------------------------------------------

    def _slot_for(self, job: ProcessingJob) -> _Slot:
        """The queue's record for a conversion, reused when it is a retry."""
        key = (job.job_id, job.playlist_id)
        existing = self._slots.get(key)
        if existing is not None:
            return existing
        slot = _Slot(job=job)
        self._slots[key] = slot
        return slot

    def _position(self, slot: _Slot) -> int | None:
        """A waiting conversion's place in line, counted from one."""
        if slot.state != "queued":
            return None
        for index, candidate in enumerate(self._waiting, start=1):
            if candidate is slot:
                return index
        return None

    def _take_available(self) -> list[_Slot]:
        """Claim slots for as many waiting conversions as the limit allows."""
        starting: list[_Slot] = []
        while self._waiting and len(self._active) < self._max_active:
            slot = self._waiting.popleft()
            if slot.state != "queued":
                continue
            slot.state = "active"
            slot.started_at = time.time()
            self._active.add(slot.key)
            starting.append(slot)
        return starting

    def _launch(self, slots: Sequence[_Slot]) -> None:
        """Start a thread per claimed slot. Each thread is one conversion."""
        for slot in slots:
            threading.Thread(
                target=self._run_slot,
                args=(slot,),
                name=f"spotm3u-queue-{slot.key[1]}",
                daemon=True,
            ).start()

    def _run_slot(self, slot: _Slot) -> None:
        """Run one conversion to a decision, then give its slot to the next."""
        job = slot.job
        try:
            job.start()
        except (JobStartError, JobNotCancellableError) as exc:
            logger.warning("queued conversion could not start: %s", exc)
        try:
            job.wait()
        finally:
            self._settle(slot)

    def _settle(self, slot: _Slot) -> None:
        """Record how a conversion ended and let the next one in."""
        with self._condition:
            status = slot.job.status
            if status == "completed":
                slot.state = "completed"
            elif status == "cancelled":
                slot.state = "cancelled"
            else:
                slot.state = "failed"
            slot.finished_at = time.time()
            self._active.discard(slot.key)
            starting = self._take_available()
            self._condition.notify_all()
        # Started outside the lock, because a conversion that starts instantly
        # would otherwise be able to ask the queue a question it cannot answer.
        self._launch(starting)

    def _reject_if_closed(self) -> None:
        if self._closed:
            raise QueueClosedError("queue is closed")
