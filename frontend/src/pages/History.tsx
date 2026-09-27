import { useEffect, useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { Button, buttonClasses } from "../components/Button";
import { EmptyState } from "../components/Panel";
import { ErrorNote, StatusNote } from "../components/Notice";
import { RunStatusIcon, statusLabel } from "../lib/status";
import { ApiError, getHistory } from "../lib/api";
import type { HistoryOrder, HistoryPage, HistoryStatus } from "../lib/api";
import { HISTORY_MAX_LIMIT, HISTORY_PAGE_SIZE, formatDuration, formatWhen, isLive, runSummary } from "../lib/history";
import { Link } from "../lib/router";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

const ORDERS: { value: HistoryOrder; label: string }[] = [
  { value: "recent", label: "Newest first" },
  { value: "oldest", label: "Oldest first" },
  { value: "name", label: "By name" },
];

/**
 * The download history: what this machine has converted, newest first, with the
 * state filter, the text search and the ordering answered by the backend. The
 * page reads stored state and never decides it, and it grows a page at a time
 * rather than listing every run ever recorded, so the list stays readable with a
 * long history behind it.
 */
export default function History() {
  useDocumentTitle("Download history - Spotify to M3U Converter");
  const [status, setStatus] = useState<HistoryStatus | "">("");
  const [order, setOrder] = useState<HistoryOrder>("recent");
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState(HISTORY_PAGE_SIZE);
  const [reload, setReload] = useState(0);
  const [page, setPage] = useState<HistoryPage | null>(null);
  const [failure, setFailure] = useState<{ message: string; code: string } | null>(null);
  const [loading, setLoading] = useState(true);

  // Typing should not ask the backend for every keystroke.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(text.trim());
      setShown(HISTORY_PAGE_SIZE);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [text]);

  // Changing a filter starts again at the first page, or "Show more" would keep
  // a window open on a list it no longer describes.
  const filterBy = (value: HistoryStatus | "") => {
    setStatus(value);
    setShown(HISTORY_PAGE_SIZE);
  };
  const sortBy = (value: HistoryOrder) => {
    setOrder(value);
    setShown(HISTORY_PAGE_SIZE);
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getHistory({ status, q: query, order, limit: shown })
      .then((result) => {
        if (cancelled) return;
        setPage(result);
        setFailure(null);
      })
      .catch((cause) => {
        if (cancelled) return;
        console.error("reading the download history failed", cause);
        setFailure({
          message:
            cause instanceof ApiError ? cause.message : "The download history could not be read.",
          code: cause instanceof ApiError ? cause.code : "unknown",
        });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [status, order, query, shown, reload]);

  // While a conversion is still running the list follows it, so opening the
  // history during a conversion shows the run move.
  const live = (page?.runs ?? []).some((run) => isLive(run.status));
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => setReload((value) => value + 1), 2000);
    return () => window.clearInterval(timer);
  }, [live]);

  const filtered = Boolean(status || query);
  const runs = page?.runs ?? [];

  return (
    <>
      <PageHeader
        eyebrow="Stored on this machine"
        title="Download history"
        intro="Every conversion this app has run here: when it ran, how many tracks it finished, where the audio went, and why a track did not make it."
      />

      {failure ? (
        failure.code === "history_unavailable" ? (
          <>
            <ErrorNote>{failure.message}</ErrorNote>
            <EmptyState
              title="There is no history to show"
              text="The processing history is off in the config.toml file, or its file cannot be read. Conversions still work either way; they are simply not recorded."
            />
          </>
        ) : (
          <ErrorNote>{failure.message}</ErrorNote>
        )
      ) : loading && !page ? (
        <StatusNote>Reading the download history…</StatusNote>
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter by state">
              <StateButton active={status === ""} onClick={() => filterBy("")} label="All" />
              {(page?.statuses ?? []).map((value) => (
                <StateButton
                  key={value}
                  active={status === value}
                  onClick={() => filterBy(value)}
                  label={statusLabel(value)}
                />
              ))}
            </div>
            <div className="ml-auto flex flex-wrap items-center gap-2">
              <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Sort runs">
                {ORDERS.map((option) => (
                  <StateButton
                    key={option.value}
                    active={order === option.value}
                    onClick={() => sortBy(option.value)}
                    label={option.label}
                  />
                ))}
              </div>
              <label className="flex items-center gap-2">
                <span className="text-sm text-ink-muted">Search</span>
                <input
                  type="search"
                  value={text}
                  autoComplete="off"
                  placeholder="Playlist, track or artist"
                  onChange={(event) => setText(event.target.value)}
                  className="w-56 rounded-sm border border-line-strong bg-surface px-2 py-1.5 text-sm text-ink placeholder:text-ink-faint"
                />
              </label>
            </div>
          </div>

          {filtered && !loading && runs.length === 0 && (
            <StatusNote>No runs match these filters — choose All to see the whole history.</StatusNote>
          )}

          {!filtered && runs.length === 0 && (
            <EmptyState
              title="No conversions recorded yet"
              text="The history fills in as playlists are converted. Import an Exportify ZIP to record the first one."
              actions={
                <Link to="/" className={buttonClasses("primary", "small")}>
                  Import a ZIP
                </Link>
              }
            />
          )}

          {runs.length > 0 && (
            <ol className="m-0 list-none rounded-md border border-line">
              {runs.map((run) => (
                <HistoryRow key={run.id} run={run} />
              ))}
            </ol>
          )}

          {runs.length > 0 && runs.length < (page?.total ?? 0) && (
            <div className="mt-4 flex flex-wrap items-center gap-3">
              {shown < HISTORY_MAX_LIMIT && (
                <Button
                  variant="secondary"
                  onClick={() => setShown((value) => value + HISTORY_PAGE_SIZE)}
                >
                  Show more
                </Button>
              )}
              <span className="text-sm text-ink-muted">
                Showing {runs.length} of {page?.total}
                {shown < HISTORY_MAX_LIMIT ? "" : " (the API serves at most 500 at a time — narrow the search to see the rest)"}
              </span>
            </div>
          )}
        </>
      )}

      <p className="mt-6 mb-0 text-sm text-ink-faint">
        This record is kept by the application in a small local file beside your settings, on this
        machine only. <code className="text-ink-muted">[history]</code> in <code className="text-ink-muted">config.toml</code>{" "}
        turns it off, bounds how many runs are kept, or moves the file.
      </p>
    </>
  );
}

function HistoryRow({ run }: { run: HistoryPage["runs"][number] }) {
  const when = formatWhen(run.finished_at ?? run.started_at);
  const duration = formatDuration(run.started_at, run.finished_at);
  return (
    <li className="border-b border-line last:border-b-0">
      <Link
        to={`/history/${run.id}`}
        className="flex items-center gap-3 p-3 text-ink no-underline transition-colors hover:bg-state-hover"
      >
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <strong className="block min-w-0 truncate text-sm font-medium">{run.playlist_name}</strong>
            {run.fast_mode && (
              <span className="rounded-sm border border-line bg-surface-subtle px-1.5 py-px text-[0.6875rem] leading-4 font-medium text-ink-muted">
                Fast mode
              </span>
            )}
          </div>
          <div className="truncate text-sm text-ink-muted">{runSummary(run)}</div>
          <div className="truncate text-xs text-ink-faint">
            {when}
            {duration ? ` · took ${duration}` : ""}
            {run.m3u_path ? " · playlist written" : ""}
          </div>
        </div>
        <span
          className="hidden shrink-0 max-w-[18rem] truncate text-xs text-ink-faint sm:block"
          title={run.output_dir}
        >
          {run.output_dir}
        </span>
        <RunStatusIcon status={run.status} />
      </Link>
    </li>
  );
}

function StateButton({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`${buttonClasses("secondary", "small")} ${active ? "bg-state-selected" : ""}`}
    >
      {label}
    </button>
  );
}
