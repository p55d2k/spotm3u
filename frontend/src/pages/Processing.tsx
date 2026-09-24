import { useCallback, useEffect, useRef, useState } from "react";
import { PageHeader, BackLink } from "../components/PageHeader";
import { Button } from "../components/Button";
import { ProgressCard } from "../components/ProgressCard";
import { TrackRow } from "../components/TrackRow";
import { ErrorNote } from "../components/Notice";
import { ApiError, getJob, getPlaylistProcessingStatus, startProcessing } from "../lib/api";
import type { JobPayload, JobState } from "../lib/api";
import { appUrl, navigate } from "../lib/router";
import { useToast } from "../components/Toast";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { EtaEstimator, progressFraction, remainingLabel } from "../lib/eta";
import { statusLabel } from "../lib/status";

type Phase = "loading" | "idle" | "active";

/**
 * The single-playlist conversion screen, migrated from ``processing.html``.
 * Until a conversion has been started the page offers the fast-mode choice
 * and the "Start converting playlist" action; once one exists it polls the
 * job every second and redraws only the rows that changed, and a completed
 * job moves straight to its result page. The ETA is the same measured-rate
 * estimate the Flask pages drew.
 */
export default function Processing({ jobId, playlistId }: { jobId: string; playlistId: string }) {
  useDocumentTitle("Processing - Spotify to M3U Converter");
  const toast = useToast();
  const [header, setHeader] = useState<JobPayload | null>(null);
  const [state, setState] = useState<JobState | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [fastMode, setFastMode] = useState(false);
  const [busy, setBusy] = useState(false);
  const mountedRef = useRef(true);
  const timerRef = useRef<number | null>(null);
  const etaRef = useRef(new EtaEstimator());

  const scheduleNext = () => {
    if (!mountedRef.current) return;
    timerRef.current = window.setTimeout(() => void pollOnce(), 1000);
  };

  const pollOnce = useCallback(async () => {
    try {
      const next = await getPlaylistProcessingStatus(jobId, playlistId);
      if (!mountedRef.current) return;
      if (next.status === "running") {
        etaRef.current.record(next.completed, next.started_at ?? 0);
        setPhase("active");
      } else if (next.status === "completed") {
        navigate(`/jobs/${jobId}/playlists/${playlistId}/result`);
        return;
      } else {
        setPhase("active");
      }
      setState(next);
      if (next.status === "running" || next.status === "queued") {
        scheduleNext();
      }
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
      // A transient network hiccup is not a state change: keep polling.
      scheduleNext();
    }
  }, [jobId, playlistId]);

  useEffect(() => {
    mountedRef.current = true;
    getJob(jobId)
      .then((payload) => {
        setHeader(payload);
        setFastMode(payload.defaults.fast_mode);
      })
      .catch(() => {
        // The status request below decides what to show; the header only needs
        // the name and total, which a running job's snapshot also provides.
      });
    void pollOnce();
    return () => {
      mountedRef.current = false;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, [jobId, pollOnce]);

  const startConverting = async () => {
    setBusy(true);
    try {
      await startProcessing(jobId, { playlist_ids: [playlistId], fast_mode: fastMode });
      setPhase("active");
      void pollOnce();
    } catch (cause) {
      setBusy(false);
      toast.error(
        "Unable to start processing. Please try again.",
        cause instanceof ApiError ? cause.message : "Check your connection and try again.",
      );
    }
  };

  const name = state?.playlist.name ?? header?.playlists.find((p) => p.id === playlistId)?.name ?? null;
  const totalTracks =
    state?.playlist.total_tracks ??
    header?.playlists.find((p) => p.id === playlistId)?.track_count ??
    null;

  return (
    <>
      <PageHeader
        title={name ? `Convert "${name}"` : "Converting playlist"}
        intro={
          totalTracks !== null
            ? `${totalTracks} tracks · You can leave this window open while SpotM3U works.`
            : "You can leave this window open while SpotM3U works."
        }
        actions={<BackLink href={appUrl(`/jobs/${jobId}/playlists`)}>Back to playlist selection</BackLink>}
      />

      {phase === "idle" && (
        <div className="mb-6 flex flex-col gap-4">
          <label className="flex cursor-pointer gap-3 border-l-2 border-accent bg-surface-subtle px-4 py-3 hover:bg-state-selected">
            <input
              type="checkbox"
              checked={fastMode}
              onChange={(event) => setFastMode(event.target.checked)}
              className="mt-1"
            />
            <span className="text-md text-ink-muted">
              <strong className="text-ink">Fast mode</strong> — find audio and write the playlist
              without source checks, audio checks or metadata (tags, artwork, lyrics). Downloads
              finish much faster, but matches can be less accurate and the MP3s stay plain.
            </span>
          </label>
          <div>
            <Button busy={busy} onClick={() => void startConverting()}>
              Start converting playlist
            </Button>
          </div>
        </div>
      )}

      {state && phase === "active" && (
        <ProgressCard
          statusLine={statusLabel(state.status)}
          percent={progressFraction(
            state.searched,
            state.completed,
            state.progress_total || state.playlist.total_tracks,
          )}
          searched={state.searched}
          completed={state.completed}
          total={state.progress_total || state.playlist.total_tracks}
          successful={state.successful}
          failed={state.failed}
          eta={state.status === "running" ? remainingLabel(state, etaRef.current) : null}
          currentTrack={
            state.current_track
              ? `Current: ${state.current_track.title} — ${statusLabel(state.current_track.status)}`
              : undefined
          }
          error={state.error}
        >
          <ol className="m-0 list-none rounded-md border border-line p-1">
            {state.tracks.map((track) => (
              <TrackRow key={track.index} track={track} jobId={jobId} playlistId={playlistId} />
            ))}
          </ol>
        </ProgressCard>
      )}

      {state && state.status === "failed" && !state.error && (
        <ErrorNote>The conversion stopped unexpectedly. Try again from the playlist selection.</ErrorNote>
      )}
    </>
  );
}