import { useEffect, useMemo, useRef, useState } from "react";
import { PageHeader, BackLink } from "../components/PageHeader";
import { Button, ButtonLink } from "../components/Button";
import { EmptyState } from "../components/Panel";
import { StatusNote, ErrorNote } from "../components/Notice";
import { ApiError, getJob, saveSelection } from "../lib/api";
import type { PlaylistSummary } from "../lib/api";
import { appUrl, navigate } from "../lib/router";
import { useToast } from "../components/Toast";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

/**
 * The playlist selection screen, migrated from ``playlists.html``: search,
 * select-all / deselect-all / convert-all, and "Convert selected". The chosen
 * subset is persisted to the job state (``PUT /selection``), then the shell
 * moves to the single or the batch processing screen — the conversion itself
 * does not start until the user confirms fast mode there.
 */
export default function Playlists({ jobId }: { jobId: string }) {
  useDocumentTitle("Select playlists - Spotify to M3U Converter");
  const toast = useToast();
  const [playlists, setPlaylists] = useState<PlaylistSummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const arrivingAt = useRef(true);

  useEffect(() => {
    if (!arrivingAt.current) return;
    arrivingAt.current = false;
    getJob(jobId)
      .then((payload) => setPlaylists(payload.playlists))
      .catch((cause) => {
        if (cause instanceof ApiError && cause.code === "job_expired") {
          toast.error(cause.message);
          navigate("/");
          return;
        }
        console.error("loading the export failed", cause);
        setLoadError(cause instanceof ApiError ? cause.message : "The export could not be read.");
      });
  }, [jobId, toast]);

  const visible = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (!term) return playlists ?? [];
    return (playlists ?? []).filter((playlist) => playlist.name.toLowerCase().includes(term));
  }, [playlists, query]);

  const toggle = (id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const selectAll = () => {
    setSelected(new Set((playlists ?? []).map((playlist) => playlist.id)));
  };

  const deselectAll = () => setSelected(new Set());

  const convert = async (ids: string[]) => {
    if (ids.length === 0) return;
    setBusy(true);
    try {
      await saveSelection(jobId, ids);
      if (ids.length === 1) {
        navigate(`/jobs/${jobId}/playlists/${ids[0]}/processing`);
      } else {
        navigate(`/jobs/${jobId}/processing`);
      }
    } catch (cause) {
      setBusy(false);
      toast.error(
        "The playlist selection could not be saved.",
        cause instanceof ApiError ? cause.message : "Try again in a moment.",
      );
    }
  };

  const convertAll = () => {
    void convert((playlists ?? []).map((playlist) => playlist.id));
  };

  if (loadError) {
    return <ErrorNote>{loadError}</ErrorNote>;
  }

  if (!playlists) {
    return <StatusNote>Reading your export…</StatusNote>;
  }

  return (
    <>
      <PageHeader
        title="Select playlists"
        actions={<BackLink href={appUrl("/")}>Import a different ZIP</BackLink>}
      />
      {playlists.length === 0 ? (
        <EmptyState
          title="No playlists in this export"
          text="The ZIP was read, but it contains no playlists to convert. Export your playlists from Exportify again, then import the new ZIP."
          actions={<ButtonLink href={appUrl("/")}>Import a different ZIP</ButtonLink>}
        />
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-end gap-3 border-b border-line pb-3">
            <label className="block min-w-40 flex-1 text-sm font-medium text-ink-muted">
              Search playlists
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search by playlist name"
                autoComplete="off"
                className="mt-1 block w-full rounded-[0.375rem] border border-line bg-surface px-3 py-2 text-md text-ink min-h-9 placeholder:text-ink-faint focus:border-accent focus:outline-none"
              />
            </label>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={selectAll}>Select all</Button>
              <Button variant="secondary" onClick={deselectAll}>Deselect all</Button>
              <Button variant="secondary" onClick={convertAll}>Convert all</Button>
              <Button busy={busy} disabled={selected.size === 0} onClick={() => void convert([...selected])}>
                Convert selected
              </Button>
            </div>
            <p role="status" className="mb-0.5 w-full text-sm text-ink-muted">
              {selected.size} selected · {visible.length} shown
            </p>
          </div>

          <div className="grid content-stretch gap-3 [grid-template-columns:repeat(auto-fill,minmax(15.25rem,1fr))]">
            {visible.map((playlist) => {
              const checked = selected.has(playlist.id);
              return (
                <label
                  key={playlist.id}
                  className={`relative flex cursor-pointer flex-col gap-1 rounded-md border bg-surface p-4 pt-3 transition-[border-color,background-color] duration-[0.12s] hover:border-line-strong ${
                    checked ? "border-accent bg-state-selected" : "border-line"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggle(playlist.id)}
                    className="absolute top-4 right-4"
                  />
                  <strong className="max-w-[calc(100%-1.75rem)] text-md font-semibold leading-tight">
                    {playlist.name}
                  </strong>
                  <span className="text-sm text-ink-muted">
                    {playlist.track_count} track{playlist.track_count === 1 ? "" : "s"}
                  </span>
                </label>
              );
            })}
          </div>
          {playlists.length > 0 && visible.length === 0 && (
            <StatusNote>No playlists match your search.</StatusNote>
          )}
          <StatusNote>
            Playlists with more than 50 songs can take a while to finish. You can leave this window
            open while SpotM3U works.
          </StatusNote>
        </>
      )}
    </>
  );
}