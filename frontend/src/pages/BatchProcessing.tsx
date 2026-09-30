import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader, BackLink } from "../components/PageHeader";
import { Button } from "../components/Button";
import { ProgressCard } from "../components/ProgressCard";
import { TrackRow } from "../components/TrackRow";
import {
  ApiError,
  cancelQueuedPlaylist,
  getJob,
  getProcessingStatus,
  startProcessing,
} from "../lib/api";
import type { BatchState } from "../lib/api";
import { appUrl, navigate } from "../lib/router";
import { useToast } from "../components/Toast";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { EtaEstimator, progressFraction, remainingLabel } from "../lib/eta";
import { QueueStatusIcon, statusLabel } from "../lib/status";
import { FastModeOption } from "../components/FastModeOption";
import { QueuePanel } from "../components/QueuePanel";

type Phase = "loading" | "idle" | "active";

/**
 * The batch conversion screen, migrated from ``batch_processing.html``. One
 * start action hands every selected playlist to the download queue, which runs a
 * bounded number at a time and holds the rest; progress is polled every 700 ms
 * and redrawn per playlist (the rows that changed, only), and a finished batch
 * moves to the batch result page. Every playlist is shown in the state the queue
 * says it is in, so waiting work never reads as a stalled conversion.
 */
export default function BatchProcessing({ jobId }: { jobId: string }) {
  useDocumentTitle("Batch processing - Spotify to M3U Converter");
  const toast = useToast();
  const [phase, setPhase] = useState<Phase>("loading");
  const [state, setState] = useState<BatchState | null>(null);
  const [fastMode, setFastMode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState<string | null>(null);
  const mountedRef = useRef(true);
  const timerRef = useRef<number | null>(null);
  const etaRef = useRef(new EtaEstimator());

  const scheduleNext = () => {
    if (!mountedRef.current) return;
    timerRef.current = window.setTimeout(() => void pollOnce(), 700);
  };

  const pollOnce = useCallback(async () => {
    try {
      const next = await getProcessingStatus(jobId);
      if (!mountedRef.current) return;
      if (next.status === "running") {
        etaRef.current.record(
          next.searched,
          next.resolved,
          next.completed,
          next.started_at ?? 0,
        );
      }
setState(next);
        setPhase("active");
      if (next.status === "completed") {
        navigate(`/jobs/${jobId}/result`);
        return;
      }
      // Work that is only waiting still needs watching: it will start on its own.
      // Work that has all settled does not, so polling stops rather than
      // reporting a finished batch as though it were still in progress.
      if (next.queue && next.queue.active === 0 && next.queue.waiting === 0) {
        if (next.status === "failed" || next.status === "cancelled") return;
      }
      scheduleNext();
    } catch (cause) {
      if (!mountedRef.current) return;
      if (cause instanceof ApiError) {
        if (cause.code === "job_expired") {
          toast.error(cause.message);
          navigate("/");
          return;
        }
        if (cause.code === "selection_expired") {
          toast.error(cause.message);
          navigate(`/jobs/${jobId}/playlists`);
          return;
        }
        if (cause.code === "job_not_found") {
          setPhase("idle");
          return;
        }
      }
      scheduleNext();
    }
  }, [jobId]);

  useEffect(() => {
    mountedRef.current = true;
    getJob(jobId)
      .then((payload) => setFastMode(payload.defaults.fast_mode))
      .catch(() => {
        // The batch status request decides what to show.
      });
    void pollOnce();
    return () => {
      mountedRef.current = false;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, [jobId, pollOnce]);

  const startBatch = async () => {
    setBusy(true);
    try {
      const next = await startProcessing(jobId, { fast_mode: fastMode });
      setState(next);
      setPhase("active");
      if (next.status === "completed") {
        navigate(`/jobs/${jobId}/result`);
      } else {
        void pollOnce();
      }
    } catch (cause) {
      setBusy(false);
      toast.error(
        "Batch processing could not be started.",
        cause instanceof ApiError ? cause.message : "Check your connection and try again.",
      );
    }
  };

  const cancelWaiting = async (playlistId: string) => {
    setCancelling(playlistId);
    try {
      await cancelQueuedPlaylist(jobId, playlistId);
      toast.success("Removed from the queue.");
    } catch (cause) {
      toast.error(
        "That playlist could not be removed from the queue.",
        cause instanceof ApiError ? cause.message : "Check your connection and try again.",
      );
    } finally {
      setCancelling(null);
      void pollOnce();
    }
  };

  const activePlaylists =
    state?.playlists.filter((playlist) => playlist.queue_state === "active") ?? [];
  const waitingPlaylists =
    state?.playlists.filter((playlist) => playlist.queue_state === "queued") ?? [];
  const currentTrack = activePlaylists.length
    ? `Converting now: ${activePlaylists.map((playlist) => playlist.playlist.name).join(", ")}`
    : waitingPlaylists.length && !activePlaylists.length
      ? `Waiting for a slot: ${waitingPlaylists.map((playlist) => playlist.playlist.name).join(", ")}`
      : undefined;

  const statusLine = state
    ? batchStatusLine(state, activePlaylists.length, waitingPlaylists.length)
    : "";

  return (
    <>
      <PageHeader
        title="Converting selected playlists"
        intro="Playlists with more than 50 songs can take a while. Leave this window open while SpotM3U completes the work."
        actions={<BackLink href={appUrl(`/jobs/${jobId}/playlists`)}>Back to playlist selection</BackLink>}
      />

      {phase === "idle" && (
        <div className="mb-6 flex flex-col gap-4">
          <FastModeOption
            checked={fastMode}
            onChange={setFastMode}
            outputLabel="each playlist"
          />
          <div>
            <Button busy={busy} onClick={() => void startBatch()}>
              Start batch processing
            </Button>
          </div>
        </div>
      )}

      {state && phase === "active" && (
        <>
          <ProgressCard
            statusLine={statusLine}
            percent={progressFraction(state.searched, state.resolved, state.completed, state.total)}
            searched={state.searched}
            resolved={state.resolved}
            completed={state.completed}
            total={state.total}
            successful={state.successful}
            failed={state.failed}
            eta={
              state.status === "running"
                ? remainingLabel(
                    {
                      status: state.status,
                      started_at: state.started_at,
                      progress_total: state.total,
                      searched: state.searched,
                      resolved: state.resolved,
                      completed: state.completed,
                    },
                    etaRef.current,
                  )
                : null
            }
            currentTrack={currentTrack}
            error={state.status === "failed" ? "Some playlists could not be converted." : null}
          >
            <div className="flex flex-col gap-4">
              {state.playlists.map((playlist) => {
                const waiting = playlist.queue_state === "queued";
                const cancelled = playlist.queue_state === "cancelled";
                return (
                <section
                key={playlist.playlist.id}
                className={`flex flex-col gap-2 rounded-lg p-3 ${
                  playlist.queue_state === "active"
                    ? "border border-accent bg-accent-subtle/40"
                    : ""
                } ${cancelled ? "opacity-70" : ""}`}
              >
                  <div className="flex items-center gap-2">
                    <h3 className="m-0 flex-1 text-base">{playlist.playlist.name}</h3>
                    {playlist.queue_state && <QueueStatusIcon state={playlist.queue_state} />}
                  </div>
                  <p className="m-0 text-sm text-ink-muted" role="status">
                    {playlist.queue_state
                      ? `${statusLabel(playlist.queue_state)} · `
                      : ""}
                    {/* A waiting playlist has searched nothing, so its stage counts
                        are shown as not started rather than as a stalled run. */}
                    {waiting
                      ? `${playlist.playlist.total_tracks} track${
                          playlist.playlist.total_tracks === 1 ? "" : "s"
                        } waiting to start`
                      : `${playlist.searched} / ${playlist.playlist.total_tracks} found · ${
                          playlist.resolved
                        } / ${playlist.playlist.total_tracks} audio ready · ${playlist.completed} / ${
                          playlist.playlist.total_tracks
                        } tracks · ${playlist.successful} successful · ${playlist.failed} failed`}
                  </p>
                  {!waiting && !cancelled && (
                    <div
                      className="h-2 overflow-hidden rounded-sm bg-track"
                      role="progressbar"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={
                        playlist.playlist.total_tracks
                          ? Math.round((playlist.completed / playlist.playlist.total_tracks) * 100)
                          : 0
                      }
                    >
                      <div
                        className="h-full rounded-sm bg-accent transition-[width] duration-300"
                        style={{
                          width: `${
                            playlist.playlist.total_tracks
                              ? Math.round((playlist.completed / playlist.playlist.total_tracks) * 100)
                              : 0
                          }%`,
                        }}
                      />
                    </div>
                  )}
                  {playlist.current_track && !waiting && (
                    <p className="m-0 text-sm text-ink-muted italic" aria-live="polite">
                      Current: {playlist.current_track.title} — {statusLabel(playlist.current_track.status)}
                    </p>
                  )}
                  <ol className="m-0 list-none rounded-md border border-line p-1">
                    {playlist.tracks.map((track) => (
                      <TrackRow
                        key={track.index}
                        track={track}
                        jobId={jobId}
                        playlistId={playlist.playlist.id}
                      />
                    ))}
                  </ol>
                </section>
                );
              })}
            </div>
          </ProgressCard>
          {state.queue && (
            <QueuePanel
              queue={state.queue}
              onCancel={(playlistId) => void cancelWaiting(playlistId)}
              cancelling={cancelling}
            />
          )}
          {state.status === "failed" && (
            <p className="m-0">
              <a className="font-medium text-accent-ink hover:underline" href={appUrl(`/jobs/${jobId}/result`)}>
                View batch results
              </a>
            </p>
          )}
        </>
      )}
    </>
  );
}

/**
 * The one line that describes the whole batch. It reads the queue state rather
 * than the track stages alone, so a batch that has been accepted but has not
 * been given a slot says so instead of claiming a stage that is not running.
 */
function batchStatusLine(
  state: BatchState,
  activeCount: number,
  waitingCount: number,
): string {
  if (state.status === "completed") return "Batch complete";
  if (state.status === "failed") return "Batch finished with errors";
  if (state.status === "cancelled") return "Batch stopped before it finished";
  if (state.status === "queued" || (waitingCount > 0 && activeCount === 0)) {
    return "Queued — waiting for a conversion slot";
  }
  const hasStage = (stage: string) =>
    state.playlists.some((playlist) =>
      playlist.tracks.some((track) => track.status === stage),
    );
  if (hasStage("downloading")) return "Downloading audio";
  if (hasStage("enriching-metadata")) return "Finalizing metadata";
  return "Searching for audio";
}