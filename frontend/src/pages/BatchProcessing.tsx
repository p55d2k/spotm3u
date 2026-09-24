import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader, BackLink } from "../components/PageHeader";
import { Button } from "../components/Button";
import { ProgressCard } from "../components/ProgressCard";
import { TrackRow } from "../components/TrackRow";
import { ApiError, getJob, getProcessingStatus, startProcessing } from "../lib/api";
import type { BatchState } from "../lib/api";
import { appUrl, navigate } from "../lib/router";
import { useToast } from "../components/Toast";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { EtaEstimator, progressFraction, remainingLabel } from "../lib/eta";
import { statusLabel } from "../lib/status";
import { FastModeOption } from "../components/FastModeOption";

type Phase = "loading" | "idle" | "active";

/**
 * The batch conversion screen, migrated from ``batch_processing.html``. One
 * start action launches every selected playlist at once; progress is polled
 * every 700 ms and redrawn per playlist (the rows that changed, only), and a
 * finished batch moves to the batch result page.
 */
export default function BatchProcessing({ jobId }: { jobId: string }) {
  useDocumentTitle("Batch processing - Spotify to M3U Converter");
  const toast = useToast();
  const [phase, setPhase] = useState<Phase>("loading");
  const [state, setState] = useState<BatchState | null>(null);
  const [fastMode, setFastMode] = useState(false);
  const [busy, setBusy] = useState(false);
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
        etaRef.current.record(next.completed, next.started_at ?? 0);
      }
      setState(next);
      setPhase("active");
      if (next.status === "completed") {
        navigate(`/jobs/${jobId}/result`);
        return;
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

  const runningPlaylists =
    state?.playlists.filter((playlist) => playlist.status === "running") ?? [];
  const currentTrack =
    runningPlaylists.length > 0
      ? `Running concurrently: ${runningPlaylists.map((playlist) => playlist.playlist.name).join(", ")}`
      : state?.status === "running"
        ? "Processing selected playlists concurrently"
        : undefined;

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
            statusLine={
              state.status === "completed"
                ? "Batch complete"
                : state.status === "failed"
                  ? "Batch finished with errors"
                  : state.playlists.some((playlist) =>
                      playlist.tracks.some((track) => track.status === "downloading"),
                    )
                    ? "Downloading audio"
                    : state.playlists.some((playlist) =>
                        playlist.tracks.some((track) => track.status === "enriching-metadata"),
                      )
                      ? "Finalizing metadata"
                      : "Searching for audio"
            }
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
              {state.playlists.map((playlist) => (
                <section
                key={playlist.playlist.id}
                className={`flex flex-col gap-2 rounded-lg p-3 ${
                  playlist.status === "running" ? "border border-accent bg-accent-subtle/40" : ""
                }`}
              >
                  <h3 className="m-0 text-base">{playlist.playlist.name}</h3>
                  <p className="m-0 text-sm text-ink-muted" role="status">
                    {statusLabel(playlist.status)} · {playlist.searched} /{" "}
                    {playlist.playlist.total_tracks} found · {playlist.resolved} /{" "}
                    {playlist.playlist.total_tracks} audio ready · {playlist.completed} /{" "}
                    {playlist.playlist.total_tracks} tracks · {playlist.successful} successful ·{" "}
                    {playlist.failed} failed
                  </p>
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
                  {playlist.current_track && (
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
              ))}
            </div>
          </ProgressCard>
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