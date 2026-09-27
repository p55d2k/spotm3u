import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import { PageHeader } from "../components/PageHeader";
import { Button, buttonClasses } from "../components/Button";
import { EmptyState } from "../components/Panel";
import { ErrorNote, StatusNote } from "../components/Notice";
import { ConversionReturn } from "../components/ConversionReturn";
import { RunStatusIcon, statusLabel } from "../lib/status";
import { ApiError, getHistory } from "../lib/api";
import type { HistoryOrder, HistoryPage, HistoryStatus } from "../lib/api";
import { HISTORY_MAX_LIMIT, HISTORY_PAGE_SIZE, formatDuration, formatWhen, isLive, runSummary } from "../lib/history";
import { Link, lastConversionPlace } from "../lib/router";
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
  // Reading the history is a detour, not a destination: the conversion the user
  // came from is still open, and leaving here should not cost them the job. Read
  // once, like the shell's own state -- it cannot change while this page is up.
  const [place] = useState(lastConversionPlace);

  return (
    <>
      <PageHeader
        eyebrow="Stored on this machine"
        title="Download history"
        intro="Every conversion this app has run here: when it ran, how many tracks it finished, where the audio went, and why a track did not make it."
        actions={place ? <ConversionReturn place={place} /> : undefined}
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
          <div className="mb-4 flex items-center gap-3">
            <StateFilter
              value={status}
              statuses={page?.statuses ?? []}
              onChange={filterBy}
            />
            <div className="ml-auto flex min-w-0 items-center gap-3">
              <div className="flex shrink-0 items-center gap-2" role="group" aria-label="Sort runs">
                {ORDERS.map((option) => (
                  <StateButton
                    key={option.value}
                    active={order === option.value}
                    onClick={() => sortBy(option.value)}
                    label={option.label}
                  />
                ))}
              </div>
              {/* The field carries its own icon and border, so it reads as a
                  field rather than as a third button with loose text beside it;
                  the visible label would only have repeated the placeholder. */}
              <div className="flex min-w-0 flex-1 items-center gap-2 rounded-sm border border-line-strong bg-surface pr-2 pl-2.5 focus-within:border-accent lg:w-56 lg:flex-none">
                <Search aria-hidden="true" className="size-4 flex-none text-ink-faint" />
                <input
                  type="search"
                  value={text}
                  autoComplete="off"
                  aria-label="Search the download history"
                  placeholder="Playlist, track or artist"
                  onChange={(event) => setText(event.target.value)}
                  className="min-w-0 flex-1 border-0 bg-transparent py-1.5 text-sm text-ink placeholder:text-ink-faint focus:outline-none"
                />
              </div>
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

/**
 * The state filter as one control. There are seven states to offer and the
 * toolbar beside them needs the room, so a row of buttons wrapped onto two lines
 * and read as a mistake; a single button that names the current state keeps the
 * whole bar on one line at any window width. The states still come from the
 * backend, so the filter cannot offer a state the model does not have.
 */
function StateFilter({
  value,
  statuses,
  onChange,
}: {
  value: HistoryStatus | "";
  statuses: readonly HistoryStatus[];
  onChange: (value: HistoryStatus | "") => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const options: { value: HistoryStatus | ""; label: string }[] = [
    { value: "", label: "All states" },
    ...statuses.map((item) => ({ value: item, label: statusLabel(item) })),
  ];
  const chosen = options.find((option) => option.value === value) ?? options[0];

  // The menu belongs to the page, not to the document, so a click or a press of
  // Escape anywhere else puts it away and gives the keyboard back.
  useEffect(() => {
    if (!open) return;
    const away = (event: MouseEvent | TouchEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("touchstart", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("touchstart", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  // The list is a listbox, so the arrow keys walk it and the selection follows
  // the highlight rather than needing a second Enter.
  const step = (delta: number) => {
    const from = options.findIndex((option) => option.value === value);
    const next = options[(from + delta + options.length) % options.length];
    onChange(next.value);
  };

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            if (!open) setOpen(true);
            else step(1);
          } else if (event.key === "ArrowUp" && open) {
            event.preventDefault();
            step(-1);
          }
        }}
        className={`${buttonClasses("secondary", "small")} bg-state-selected`}
      >
        {chosen.label}
        <ChevronDown aria-hidden="true" className="size-4 text-ink-muted" />
      </button>
      {open && (
        <ul
          role="listbox"
          aria-label="Filter by state"
          className="absolute top-[calc(100%+0.25rem)] left-0 z-20 m-0 max-h-72 min-w-44 list-none overflow-y-auto rounded-md border border-line-strong bg-surface p-1 shadow-popover"
        >
          {options.map((option) => (
            <li key={option.value || "all"}>
              <button
                type="button"
                role="option"
                aria-selected={option.value === value}
                onClick={() => {
                  onChange(option.value);
                  setOpen(false);
                  buttonRef.current?.focus();
                }}
                className={`flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm transition-colors ${
                  option.value === value
                    ? "bg-state-selected font-semibold text-ink"
                    : "text-ink-muted hover:bg-state-hover hover:text-ink"
                }`}
              >
                <span className="inline-flex size-4 shrink-0 items-center justify-center">
                  {option.value === value && <Check aria-hidden="true" className="size-3.5" />}
                </span>
                {option.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
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
