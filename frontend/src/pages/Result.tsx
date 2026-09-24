import { useEffect, useMemo, useState } from "react";
import { PageHeader, BackLink } from "../components/PageHeader";
import { buttonClasses, Button, ButtonLink } from "../components/Button";
import { EmptyState } from "../components/Panel";
import { TrackRow } from "../components/TrackRow";
import { SavePlaylistButton } from "../components/SavePlaylist";
import { ErrorNote, StatusNote } from "../components/Notice";
import { ApiError, addPlaylistToMediaPlayer, getPlaylistResult, retryProcessing } from "../lib/api";
import type { MediaPlayerAction, PlaylistResult } from "../lib/api";
import { appUrl, navigate } from "../lib/router";
import { useToast } from "../components/Toast";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

type TrackFilter = "all" | "failed";

/**
 * One playlist's outcome, migrated from ``result.html``: the summary counts,
 * the track list with its All / Failed-or-ambiguous filter, the actions
 * (save M3U, retry the unresolved tracks, hand the playlist to the media
 * player), and the empty states for a conversion that produced nothing. The
 * media-player note is filled from the handoff response, exactly where the
 * Flask page flashed it after posting the same action.
 */
export default function Result({
  jobId,
  playlistId,
  fromBatch,
}: {
  jobId: string;
  playlistId: string;
  fromBatch: boolean;
}) {
  useDocumentTitle("Playlist result - Spotify to M3U Converter");
  const toast = useToast();
  const [loadError, setLoadError] = useState<string | null>(null);
  const [state, setState] = useState<PlaylistResult | null>(null);
  const [mediaNote, setMediaNote] = useState<MediaPlayerAction | null>(null);
  const [mediaError, setMediaError] = useState<string | null>(null);
  const [mediaBusy, setMediaBusy] = useState(false);
  const [retryBusy, setRetryBusy] = useState(false);
  const [filter, setFilter] = useState<TrackFilter>("all");

  useEffect(() => {
    let cancelled = false;
    getPlaylistResult(jobId, playlistId)
      .then((result) => {
        if (!cancelled) setState(result);
      })
      .catch((cause) => {
        if (cancelled) return;
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
        }
        console.error("loading the result failed", cause);
        setLoadError(
          cause instanceof ApiError ? cause.message : "The result could not be read. Try again in a moment.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [jobId, playlistId, toast]);

  const retry = async () => {
    setRetryBusy(true);
    try {
      await retryProcessing(jobId, playlistId);
      setMediaNote(null);
      setMediaError(null);
      const result = await getPlaylistResult(jobId, playlistId);
      setState(result);
      setFilter("all");
    } catch (cause) {
      toast.error(
        "The retry could not be started.",
        cause instanceof ApiError ? cause.message : "Try again in a moment.",
      );
    } finally {
      setRetryBusy(false);
    }
  };

  const addToMediaPlayer = async () => {
    setMediaBusy(true);
    setMediaError(null);
    try {
      const response = await addPlaylistToMediaPlayer(jobId, playlistId);
      setMediaNote(response.media_player);
    } catch (cause) {
      setMediaNote(null);
      setMediaError(cause instanceof ApiError ? cause.message : "The playlist could not be added.");
    } finally {
      setMediaBusy(false);
    }
  };

  const unresolved = useMemo(
    () => (state?.tracks ?? []).filter((track) => track.status !== "complete").length,
    [state],
  );
  const retryable = (state?.stale_outputs ?? 0) + unresolved;
  const failedTracks = useMemo(
    () =>
      (state?.tracks ?? []).filter(
        (track) => track.resolution === "failed" || track.resolution === "ambiguous",
      ),
    [state],
  );
  const counts = state?.counts;
  const shownTracks = filter === "failed" ? failedTracks : state?.tracks ?? [];

  if (loadError) {
    return <ErrorNote>{loadError}</ErrorNote>;
  }

  if (!state) {
    return <StatusNote>Reading the completed conversion…</StatusNote>;
  }

  return (
    <>
      <PageHeader
        title={state.playlist.name}
        intro={`Total tracks: ${state.playlist.total_tracks}`}
        actions={
          fromBatch ? (
            <BackLink href={appUrl(`/jobs/${jobId}/result`)}>Back to all playlist results</BackLink>
          ) : undefined
        }
      />

      {state.error && <ErrorNote>Processing failed: {state.error}</ErrorNote>}
      {mediaError ? (
        <ErrorNote>Add to Media Player failed: {mediaError}</ErrorNote>
      ) : (
        mediaNote && (
          <StatusNote>
            {mediaNote.cancelled
              ? mediaNote.message
              : `${mediaNote.message}${mediaNote.unresolved ? ` ${mediaNote.unresolved} unresolved track(s) were not sent.` : ""}`}
          </StatusNote>
        )
      )}
      {state.stale_outputs > 0 && (
        <ErrorNote>
          {state.stale_outputs} track{state.stale_outputs === 1 ? "" : "s"} from this playlist{" "}
          {state.stale_outputs === 1 ? "is" : "are"} no longer in the download folder. Retry to download{" "}
          {state.stale_outputs === 1 ? "it" : "them"} again.
        </ErrorNote>
      )}

      <div className="mb-2 flex flex-wrap items-center gap-2">
        {state.m3u_path && (
          <SavePlaylistButton
            jobId={jobId}
            playlistId={playlistId}
            m3uUrl={state.m3u_url}
            downloadName={state.playlist.name}
          />
        )}
        {retryable > 0 && (
          <Button variant="secondary" busy={retryBusy} onClick={() => void retry()}>
            Retry {retryable} unresolved track{retryable === 1 ? "" : "s"}
          </Button>
        )}
        {state.m3u_path && (counts?.successful ?? 0) > 0 && state.media_player_available && (
          <Button variant="secondary" busy={mediaBusy} onClick={() => void addToMediaPlayer()}>
            Add to Media Player
          </Button>
        )}
      </div>

      {!state.m3u_path && (
        <EmptyState
          title="No playlist was generated"
          text="This conversion did not finish, so there is no M3U to save. Retry the unresolved tracks above, or import a different export."
        />
      )}
      {state.m3u_path && counts && counts.successful === 0 && state.tracks.length > 0 && (
        <EmptyState
          title="No tracks matched"
          text="Nothing in this playlist was found locally or downloaded, so there is nothing to play. Retry the unresolved tracks above, check that your music library folder is correct, or import a different export."
        />
      )}

      <section aria-labelledby="result-heading" className="mt-6">
        <h2 id="result-heading" className="m-0 mb-3 text-lg">
          Summary
        </h2>
        <dl className="m-0 grid grid-cols-[repeat(auto-fit,minmax(9rem,1fr))] gap-3">
          <Stat label="Successfully resolved" value={counts?.successful ?? 0} />
          <Stat label="Local matches" value={counts?.local ?? 0} />
          <Stat label="Downloaded" value={counts?.downloaded ?? 0} />
          <Stat label="Ambiguous" value={counts?.ambiguous ?? 0} />
          <Stat label="Rejected" value={counts?.rejected ?? 0} />
          <Stat label="Missing" value={counts?.missing ?? 0} />
          <Stat label="Failed" value={counts?.failed ?? 0} />
          <Stat label="Uncertain" value={counts?.uncertain ?? 0} />
          {state.stale_outputs > 0 && <Stat label="Missing from disk" value={state.stale_outputs} />}
        </dl>
      </section>

      <section aria-labelledby="tracks-heading" className="mt-6">
        <h2 id="tracks-heading" className="m-0 mb-3 text-lg">
          Track results
        </h2>
        {state.tracks.length === 0 ? (
          <EmptyState
            title="No tracks in this playlist"
            text="This playlist is empty, so there is nothing to match or download. Choose another playlist from this ZIP, or import a different export, using the options below."
          />
        ) : (
          <>
            {failedTracks.length > 0 && (
              <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label="Filter track results">
                <FilterButton
                  active={filter === "all"}
                  onClick={() => setFilter("all")}
                  label="All tracks"
                  count={state.tracks.length}
                />
                <FilterButton
                  active={filter === "failed"}
                  onClick={() => setFilter("failed")}
                  label="Failed or ambiguous"
                  count={failedTracks.length}
                />
              </div>
            )}
            <ol className="m-0 list-none rounded-md border border-line p-1">
              {shownTracks.map((track) => (
                <TrackRow
                  key={track.index}
                  track={track}
                  jobId={jobId}
                  playlistId={playlistId}
                  lyricsBadge
                  fileMissingReason
                />
              ))}
            </ol>
            {shownTracks.length === 0 && (
              <StatusNote>No tracks match this filter — choose All tracks to see the whole playlist.</StatusNote>
            )}
          </>
        )}
      </section>

      <section aria-labelledby="next-heading" className="mt-6 flex flex-col gap-3">
        <h2 id="next-heading" className="m-0 text-lg">
          Convert another playlist
        </h2>
        <p className="m-0 text-sm text-ink-muted">
          Choose another playlist from this ZIP, or start over with a different one.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <ButtonLink href={appUrl(`/jobs/${jobId}/playlists`)}>Convert another playlist</ButtonLink>
          <ButtonLink variant="secondary" href={appUrl("/")}>
            Import another ZIP
          </ButtonLink>
        </div>
      </section>
    </>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-line bg-surface p-3">
      <dt className="text-xs font-medium tracking-[0.04em] text-ink-muted uppercase">{label}</dt>
      <dd className="mt-1 m-0 text-lg font-semibold">{value}</dd>
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