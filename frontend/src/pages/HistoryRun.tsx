import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BackLink, PageHeader } from "../components/PageHeader";
import { Button, buttonClasses } from "../components/Button";
import { EmptyState } from "../components/Panel";
import { ErrorNote, StatusNote } from "../components/Notice";
import { HistoryTrackRow } from "../components/HistoryTrackRow";
import { statusLabel } from "../lib/status";
import { ApiError, getHistoryRun, retryProcessing } from "../lib/api";
import type { HistoryRun } from "../lib/api";
import { formatDuration, formatWhen } from "../lib/history";
import { Link, appUrl } from "../lib/router";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useToast } from "../components/Toast";

type TrackFilter = "all" | "unfinished";

/**
 * One stored conversion, as the backend recorded it: the counts per state, the
 * folder it wrote to, the playlist file, and every track with its outcome, the
 * source it used, and the reason it did not make it. A finished track whose
 * audio has since been deleted says so here too — the history is a record of
 * what happened, and a file that is gone did not get there because of the
 * conversion.
 *
 * The tracks that are still missing something can be ticked and retried from
 * here, so the history is not only a record but somewhere the work can be
 * picked up again. A retry needs the upload that produced the run, so a run
 * whose upload has expired says that instead of failing obscurely: the ZIP has
 * to be imported again before anything can be retried.
 */
export default function HistoryRun({ runId }: { runId: string }) {
  const [run, setRun] = useState<HistoryRun | null>(null);
  const [failure, setFailure] = useState<{ message: string; code: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<TrackFilter>("all");
  const [selected, setSelected] = useState<readonly number[]>([]);
  const [retryBusy, setRetryBusy] = useState(false);
  const [retryNote, setRetryNote] = useState<{ ok: boolean; text: string } | null>(null);
  const toast = useToast();
  const mountedRef = useRef(true);
  const timerRef = useRef<number | null>(null);

  useDocumentTitle(
    run ? `${run.playlist_name} - Download history` : "Download history - Spotify to M3U Converter",
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, []);

  const read = useCallback(() => {
    setLoading(true);
    getHistoryRun(runId)
      .then((result) => {
        if (!mountedRef.current) return;
        setRun(result);
        setFailure(null);
      })
      .catch((cause) => {
        if (!mountedRef.current) return;
        console.error("reading the stored run failed", cause);
        setFailure({
          message: cause instanceof ApiError ? cause.message : "This run could not be read.",
          code: cause instanceof ApiError ? cause.code : "unknown",
        });
      })
      .finally(() => {
        if (mountedRef.current) setLoading(false);
      });
  }, [runId]);

  useEffect(read, [read]);

  const unfinished = useMemo(
    () => (run?.tracks ?? []).filter((track) => track.status !== "completed"),
    [run],
  );
  const missing = useMemo(
    () => (run?.tracks ?? []).filter((track) => track.file_missing).length,
    [run],
  );
  // A track is worth retrying when it never resolved, or when it did and the
  // file it wrote is since gone. A completed track with its audio still in
  // place is finished, and retrying it would download a second copy.
  const retryable = useMemo(
    () =>
      (run?.tracks ?? []).filter(
        (track) => track.status !== "completed" || track.file_missing,
      ),
    [run],
  );

  const toggle = (position: number) =>
    setSelected((current) =>
      current.includes(position)
        ? current.filter((item) => item !== position)
        : [...current, position],
    );

  const retrySelected = async () => {
    setRetryBusy(true);
    setRetryNote(null);
    try {
      await retryProcessing(run!.job_id, run!.playlist_id, selected);
      setSelected([]);
      setRetryNote({
        ok: true,
        text: "Retrying. The rows below change as each track finishes, and every earlier attempt is kept.",
      });
    } catch (cause) {
      const expired = cause instanceof ApiError && cause.code === "job_expired";
      setRetryNote(
        expired
          ? {
              ok: false,
              text: "That upload has expired, so this run can no longer be retried. Import the ZIP again to convert it anew.",
            }
          : {
              ok: false,
              text:
                cause instanceof ApiError
                  ? cause.message
                  : "The retry could not be started. Try again in a moment.",
            },
      );
      setRetryBusy(false);
      return;
    }
    const schedule = () => {
      if (!mountedRef.current) return;
      timerRef.current = window.setTimeout(() => followRetry(schedule), 1000);
    };
    schedule();
  };

  /**
   * A retry runs in the background, so the history is re-read until the run
   * settles. Each attempt stays in the record, so re-reading the run is enough
   * to see the new state.
   */
  const followRetry = (schedule: () => void) => {
    getHistoryRun(runId)
      .then((result) => {
        if (!mountedRef.current) return;
        setRun(result);
        if (result.status === "processing" || result.status === "queued") {
          schedule();
          return;
        }
        setRetryBusy(false);
      })
      .catch(() => {
        if (!mountedRef.current) return;
        setRetryBusy(false);
        toast.error(
          "The retry could not be followed.",
          "The history will show its outcome when it settles.",
        );
      });
  };

  if (failure) {
    return (
      <>
        <BackLink href={appUrl("/history")}>Back to the download history</BackLink>
        {failure.code === "history_not_found" ? (
          <EmptyState
            title="This run is no longer in the history"
            text="Only the most recent conversions are kept, so an older run may have been dropped. Import the export again to convert it anew."
            actions={
              <Link to={appUrl("/")} className={buttonClasses("primary", "small")}>
                Import a ZIP
              </Link>
            }
          />
        ) : (
          <ErrorNote>{failure.message}</ErrorNote>
        )}
      </>
    );
  }

  if (loading || !run) {
    return <StatusNote>Reading the stored conversion…</StatusNote>;
  }

  const shown = filter === "unfinished" ? unfinished : run.tracks;
  const when = formatWhen(run.finished_at ?? run.started_at);
  const duration = formatDuration(run.started_at, run.finished_at);

  return (
    <>
      <PageHeader
        eyebrow="Download history"
        title={run.playlist_name}
        intro={
          <>
            {run.total_tracks} track{run.total_tracks === 1 ? "" : "s"} · {statusLabel(run.status)}
            {when ? ` on ${when}` : ""}
            {duration ? ` · took ${duration}` : ""}
          </>
        }
        actions={
          <BackLink href={appUrl("/history")}>Back to the download history</BackLink>
        }
      />

      {run.error && <ErrorNote>This run stopped: {run.error}</ErrorNote>}
      {missing > 0 && (
        <ErrorNote>
          {missing} of the {run.total_tracks} files this run wrote{" "}
          {missing === 1 ? "is" : "are"} no longer in the download folder. Retry
          {missing === 1 ? " it" : " them"} below to write the missing audio again.
        </ErrorNote>
      )}
      {retryNote &&
        (retryNote.ok ? (
          <StatusNote>{retryNote.text}</StatusNote>
        ) : (
          <ErrorNote>{retryNote.text}</ErrorNote>
        ))}
      {retryable.length > 0 && !retryBusy && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button
            variant="ghost"
            size="small"
            onClick={() =>
              setSelected(
                selected.length === retryable.length
                  ? []
                  : retryable.map((track) => track.position),
              )
            }
          >
            {selected.length === retryable.length
              ? "Clear selection"
              : `Select all ${retryable.length} to retry`}
          </Button>
          {selected.length > 0 && (
            <Button busy={retryBusy} onClick={() => void retrySelected()}>
              Retry {selected.length} selected track{selected.length === 1 ? "" : "s"}
            </Button>
          )}
          {selected.length === 0 && (
            <span className="text-sm text-ink-muted">
              Tick the tracks to try again, or the ones whose files are gone. Tracks that already
              downloaded their audio are not offered.
            </span>
          )}
        </div>
      )}
      {retryBusy && (
        <StatusNote>
          Retrying — the rows below change as each track finishes, and every earlier attempt is kept
          under its track.
        </StatusNote>
      )}

      <section aria-labelledby="run-summary" className="mt-2">
        <h2 id="run-summary" className="m-0 mb-3 text-lg">
          Summary
        </h2>
        <dl className="m-0 grid grid-cols-[repeat(auto-fit,minmax(9rem,1fr))] gap-3">
          <Stat label="Finished" value={run.counts.completed} />
          <Stat label="Failed" value={run.counts.failed} />
          <Stat label="Cancelled" value={run.counts.cancelled} />
          <Stat label="Skipped" value={run.counts.skipped} />
          <Stat label="In progress" value={run.counts.processing} />
          <Stat label="Waiting" value={run.counts.queued} />
        </dl>
        <dl className="m-0 mt-4 grid gap-2 text-sm">
          <div>
            <dt className="inline text-ink-muted">Download folder: </dt>
            <dd className="m-0 inline break-all text-ink">{run.output_dir}</dd>
          </div>
          <div>
            <dt className="inline text-ink-muted">Playlist file: </dt>
            <dd className="m-0 inline break-all text-ink">
              {run.m3u_path || "none — this run did not finish writing one"}
            </dd>
          </div>
          {run.fast_mode && (
            <div>
              <dt className="inline text-ink-muted">Mode: </dt>
              <dd className="m-0 inline text-ink">fast mode</dd>
            </div>
          )}
        </dl>
      </section>

      <section aria-labelledby="run-tracks" className="mt-6">
        <h2 id="run-tracks" className="m-0 mb-3 text-lg">
          Tracks
        </h2>
        {run.tracks.length === 0 ? (
          <EmptyState
            title="No tracks were recorded"
            text="This run has no stored tracks. That happens when a conversion was stopped before its first track was picked up."
          />
        ) : (
          <>
            {unfinished.length > 0 && (
              <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label="Filter tracks">
                <FilterButton
                  active={filter === "all"}
                  onClick={() => setFilter("all")}
                  label="All tracks"
                  count={run.tracks.length}
                />
                <FilterButton
                  active={filter === "unfinished"}
                  onClick={() => setFilter("unfinished")}
                  label="Not finished"
                  count={unfinished.length}
                />
              </div>
            )}
            <ol className="m-0 list-none rounded-md border border-line">
              {shown.map((track) => (
                <HistoryTrackRow
                  key={track.position}
                  track={track}
                  retryable={!retryBusy && (track.status !== "completed" || track.file_missing)}
                  selected={selected.includes(track.position)}
                  onToggle={toggle}
                />
              ))}
            </ol>
            {shown.length === 0 && (
              <StatusNote>Every track in this run finished — choose All tracks to see them.</StatusNote>
            )}
          </>
        )}
      </section>
    </>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="border-b border-line pb-2">
      <dt className="text-xs font-medium tracking-[0.04em] text-ink-muted uppercase">{label}</dt>
      <dd className="mt-1 m-0 text-lg font-semibold tabular-nums">{value}</dd>
    </div>
  );
}

function FilterButton({
  active,
  onClick,
  label,
  count,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`${buttonClasses("secondary", "small")} ${active ? "bg-state-selected" : ""}`}
    >
      {label}&nbsp;<span className="text-ink-faint">{count}</span>
    </button>
  );
}
