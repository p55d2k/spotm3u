import { useEffect, useMemo, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { Button, ButtonLink } from "../components/Button";
import { EmptyState } from "../components/Panel";
import { SavePlaylistButton } from "../components/SavePlaylist";
import { ErrorNote, StatusNote } from "../components/Notice";
import { ApiError, apiUrl, addBatchToMediaPlayer, addPlaylistToMediaPlayer, getJobResult } from "../lib/api";
import type { BatchImportResponse, JobResult, MediaPlayerAction } from "../lib/api";
import { appUrl, navigate } from "../lib/router";
import { useToast } from "../components/Toast";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

/**
 * The batch outcome, migrated from ``batch_result.html``: one card per playlist
 * with its save / media-player / details actions, a success-tinted panel that
 * adds every playlist to the library in one click, and the per-playlist report
 * of that import. The "View track details" link opens a single result page with
 * the back-link that Flask drew for the same entry point.
 */
export default function BatchResult({ jobId }: { jobId: string }) {
  useDocumentTitle("Batch results - Spotify to M3U Converter");
  const toast = useToast();
  const [loadError, setLoadError] = useState<string | null>(null);
  const [result, setResult] = useState<JobResult | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkOutcome, setBulkOutcome] = useState<BatchImportResponse | null>(null);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const [playerNotes, setPlayerNotes] = useState<Record<string, { ok: boolean; text: string }>>({});

  useEffect(() => {
    let cancelled = false;
    getJobResult(jobId)
      .then((value) => {
        if (!cancelled) setResult(value);
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
        console.error("loading the batch results failed", cause);
        setLoadError(
          cause instanceof ApiError ? cause.message : "The result could not be read. Try again in a moment.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [jobId, toast]);

  const importable = useMemo(
    () =>
      (result?.playlists ?? []).filter(
        (playlist) => playlist.m3u_path && (playlist.counts?.successful ?? 0) > 0,
      ),
    [result],
  );
  const libraryName = result?.library_name ?? "the media player";

  const addAll = async () => {
    setBulkBusy(true);
    setBulkError(null);
    try {
      setBulkOutcome(await addBatchToMediaPlayer(jobId));
    } catch (cause) {
      setBulkError(cause instanceof ApiError ? cause.message : "The playlists could not be added.");
    } finally {
      setBulkBusy(false);
    }
  };

  const addOne = async (playlistId: string) => {
    setPending((current) => ({ ...current, [playlistId]: true }));
    try {
      const response = await addPlaylistToMediaPlayer(jobId, playlistId);
      setResult((current) => {
        if (!current) return current;
        return {
          ...current,
          playlists: current.playlists.map((playlist) =>
            playlist.playlist.id === playlistId ? response : playlist,
          ),
        };
      });
      setPlayerNotes((notes) => ({
        ...notes,
        [playlistId]: playerNote(response.media_player),
      }));
    } catch (cause) {
      setPlayerNotes((notes) => ({
        ...notes,
        [playlistId]: {
          ok: false,
          text: cause instanceof ApiError ? cause.message : "The playlist could not be added.",
        },
      }));
    } finally {
      setPending((current) => ({ ...current, [playlistId]: false }));
    }
  };

  if (loadError) {
    return <ErrorNote>{loadError}</ErrorNote>;
  }

  if (!result) {
    return <StatusNote>Reading the completed conversions…</StatusNote>;
  }

  return (
    <>
      <PageHeader title="Playlist results" />

      {result.library_import_available && importable.length > 0 && (
        <section
          aria-labelledby="batch-import-heading"
          className="mb-6 rounded-lg border border-success-border bg-success-subtle p-5"
        >
          <h2 id="batch-import-heading" className="m-0 mb-2 text-lg">
            Add every playlist to {libraryName}
          </h2>
          <p className="m-0 text-sm text-ink-muted">
            One click imports each of the {importable.length} playlist
            {importable.length === 1 ? "" : "s"} below into {libraryName} as{" "}
            {importable.length === 1 ? "its" : "their"} own playlist. Nothing is merged, and
            playlists are imported one after another; a playlist that fails does not stop the others.
          </p>
          <div className="mt-4">
            <Button busy={bulkBusy} onClick={() => void addAll()}>
              Add all {importable.length} playlist{importable.length === 1 ? "" : "s"} to {libraryName}
            </Button>
          </div>
        </section>
      )}
      {bulkError && <ErrorNote>Add to Media Player failed: {bulkError}</ErrorNote>}
      {bulkOutcome && (
        <section
          aria-labelledby="batch-import-result-heading"
          className="mb-6 rounded-lg border border-line bg-surface p-5"
        >
          <h2 id="batch-import-result-heading" className="m-0 mb-2 text-lg">
            Media player import
          </h2>
          {bulkOutcome.partial ? (
            <p role="alert" className="mb-2 rounded-sm border border-danger-border bg-danger-subtle px-3 py-2 text-sm text-danger-ink">
              {bulkOutcome.message}
            </p>
          ) : (
            <p role="status" className="m-0 mb-2 text-md">
              {bulkOutcome.message}
            </p>
          )}
          <ul className="m-0 mt-4 list-none p-0">
            {bulkOutcome.playlists.map((entry) => (
              <li
                key={entry.playlist_id}
                className={
                  "flex items-baseline gap-3 border-b border-line px-0 py-2 last:border-b-0 " +
                  (entry.error ? "text-danger-ink" : "")
                }
              >
                <strong className="min-w-0 flex-none text-sm font-semibold">{entry.name}</strong>
                <span className="text-sm text-ink-muted">
                  {entry.error
                    ? entry.error
                    : entry.cancelled
                      ? `Cancelled — the playlist in ${libraryName} was left unchanged.`
                      : `${entry.imported} track${entry.imported === 1 ? "" : "s"} imported${entry.skipped ? `, ${entry.skipped} already present` : ""}${entry.failed ? `, ${entry.failed} failed` : ""}.`}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {result.playlists.map((playlist) => {
        const counts = playlist.counts;
        const note = playerNotes[playlist.playlist.id];
        return (
          <section key={playlist.playlist.id} className="mb-6 flex flex-col gap-2">
            <h2 className="m-0 text-lg">{playlist.playlist.name}</h2>
            <p className="m-0 text-sm text-ink-muted">
              {playlist.successful} successful, {playlist.failed} failed of{" "}
              {playlist.playlist.total_tracks} track{playlist.playlist.total_tracks === 1 ? "" : "s"}.
            </p>
            {playlist.tracks.length > 0 && (counts?.successful ?? 0) === 0 && (
              <EmptyState
                title="No tracks matched"
                text="Nothing in this playlist was found locally or downloaded, so there is no playlist to save."
              />
            )}
            {playlist.stale_outputs > 0 && (
              <ErrorNote>
                {playlist.stale_outputs} track{playlist.stale_outputs === 1 ? "" : "s"}{" "}
                {playlist.stale_outputs === 1 ? "is" : "are"} no longer in the download folder. Retry
                on the track details page to download {playlist.stale_outputs === 1 ? "it" : "them"}{" "}
                again.
              </ErrorNote>
            )}
            {note &&
              (note.ok ? (
                <StatusNote>{note.text}</StatusNote>
              ) : (
                <ErrorNote>Add to Media Player failed: {note.text}</ErrorNote>
              ))}
            <div className="flex flex-wrap items-center gap-2">
              {playlist.m3u_path && (
                <SavePlaylistButton
                  jobId={jobId}
                  playlistId={playlist.playlist.id}
                  m3uUrl={apiUrl(`/jobs/${jobId}/playlists/${playlist.playlist.id}/m3u`)}
                  downloadName={playlist.playlist.name}
                />
              )}
              {playlist.m3u_path && (counts?.successful ?? 0) > 0 && result.media_player_available && (
                <Button
                  variant="secondary"
                  busy={pending[playlist.playlist.id] === true}
                  onClick={() => void addOne(playlist.playlist.id)}
                >
                  Add to Media Player
                </Button>
              )}
              <ButtonLink
                variant="secondary"
                href={appUrl(`/jobs/${jobId}/playlists/${playlist.playlist.id}/result?batch=1`)}
              >
                View track details
              </ButtonLink>
            </div>
          </section>
        );
      })}

      <section aria-labelledby="next-heading" className="mt-6 flex flex-col gap-3">
        <h2 id="next-heading" className="m-0 text-lg">
          Convert another playlist
        </h2>
        <p className="m-0 text-sm text-ink-muted">
          Pick more playlists from this ZIP, or start over with a different one.
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

function playerNote(action: MediaPlayerAction): { ok: boolean; text: string } {
  if (action.cancelled) {
    return { ok: true, text: action.message };
  }
  return {
    ok: true,
    text: `${action.message}${action.unresolved ? ` ${action.unresolved} unresolved track(s) were not sent.` : ""}`,
  };
}