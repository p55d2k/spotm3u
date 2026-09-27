import { useState } from "react";
import { ChevronDown, ChevronRight, Music } from "lucide-react";
import type { HistoryTrack } from "../lib/api";
import { RunStatusIcon, statusLabel } from "../lib/status";
import { formatWhen } from "../lib/history";

/**
 * One stored track of a finished conversion. The row answers the questions the
 * history exists for: what the track was, how it ended, where its audio went,
 * and why it failed if it did. The stored stage and timestamps are shown as the
 * record they are, so a track that stopped mid-download reads as one that never
 * finished rather than as a silent gap.
 *
 * A track that was retried keeps every attempt, so the row can open the list of
 * them: what each attempt decided and why it gave up. Without that, a retry
 * would erase the record of what went wrong the first time.
 */
export function HistoryTrackRow({
  track,
  retryable = false,
  selected = false,
  onToggle = undefined,
}: {
  track: HistoryTrack;
  /** Offer a retry, for a track that has nothing usable left. */
  retryable?: boolean;
  /** Whether the track is ticked for a retry. */
  selected?: boolean;
  /** Tick or untick the track for a retry. */
  onToggle?: (position: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const resolutionTag = track.resolution ? statusLabel(track.resolution) : "";
  const reasons = [track.reason, track.error].filter((text) => text && text !== track.resolution);
  // The stage a track was last seen at only says something about a track that
  // never finished; for a resolved one the resolution is the outcome.
  const stage = track.status === "completed" ? "" : track.stage;
  const when = formatWhen(track.finished_at ?? track.started_at ?? track.queued_at);
  // The last attempt is the track as it is now, which the row already shows, so
  // the list is only worth opening when there is something before it.
  const earlier = track.attempts.slice(0, -1);

  return (
    <li className="flex items-start gap-3 border-b border-line p-3 last:border-b-0">
      {retryable && onToggle && (
        <input
          type="checkbox"
          className="mt-1 size-4 flex-none"
          checked={selected}
          onChange={() => onToggle(track.position)}
          aria-label={`Retry ${track.title}`}
        />
      )}
      <span aria-hidden="true" className="mt-0.5 w-6 flex-none text-right text-sm text-ink-faint tabular-nums">
        {track.position + 1}
      </span>
      <div
        aria-hidden="true"
        className="mt-0.5 flex size-12 flex-none items-center justify-center rounded-sm bg-accent-subtle text-accent-ink"
      >
        <Music className="size-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <strong className="block min-w-0 truncate text-sm font-medium">{track.title}</strong>
          {track.retry_count > 0 && (
            <span className="rounded-sm border border-line bg-surface-subtle px-1.5 py-px text-[0.6875rem] leading-4 font-medium text-ink-muted">
              Retried {track.retry_count}×
            </span>
          )}
        </div>
        {(track.artists || track.album) && (
          <div className="truncate text-sm text-ink-muted">
            {[track.artists, track.album].filter(Boolean).join(" · ")}
          </div>
        )}
        {(resolutionTag || reasons.length > 0 || stage) && (
          <div className="mt-1 flex min-w-0 flex-wrap items-center gap-1">
            {resolutionTag && (
              <span
                className={`rounded-sm border px-1.5 py-px text-[0.6875rem] leading-4 font-medium ${tagClass(track.resolution, track.status)}`}
              >
                {resolutionTag}
              </span>
            )}
            {reasons.map((reason) => (
              <span
                key={reason}
                className="rounded-sm border border-line-strong bg-surface-subtle px-1.5 py-px text-[0.6875rem] leading-4 font-medium text-ink-muted"
              >
                {reason}
              </span>
            ))}
            {stage && (
              <span className="rounded-sm border border-line bg-surface-subtle px-1.5 py-px text-[0.6875rem] leading-4 font-medium text-ink-faint">
                Stopped at {statusLabel(stage).toLowerCase()}
              </span>
            )}
          </div>
        )}
        {track.file_missing && (
          <p className="mt-1 mb-0 text-xs font-medium text-danger-ink">
            The audio file is no longer in the download folder.
          </p>
        )}
        <div className="mt-1 min-w-0 text-xs text-ink-faint">
          {when ? <span className="mr-2">{when}</span> : null}
          {track.output_path ? (
            <span className="block break-all" title={track.output_path}>
              Written to {track.output_path}
            </span>
          ) : track.source_url ? (
            <span className="block break-all" title={track.source_url}>
              Source {track.source_url}
            </span>
          ) : null}
        </div>
        {earlier.length > 0 && (
          <>
            <button
              type="button"
              className="mt-1 inline-flex items-center gap-1 rounded-sm border border-line bg-surface-subtle px-1.5 py-px text-[0.6875rem] leading-4 font-medium text-ink-muted"
              aria-expanded={open}
              onClick={() => setOpen((value) => !value)}
            >
              {open ? (
                <ChevronDown className="size-3" aria-hidden="true" />
              ) : (
                <ChevronRight className="size-3" aria-hidden="true" />
              )}
              {open ? "Hide" : "Show"} {earlier.length} earlier attempt
              {earlier.length === 1 ? "" : "s"}
            </button>
            {open && (
              <ol className="m-0 mt-1 list-none border-l-2 border-line pl-3">
                {earlier.map((attempt) => (
                  <li key={attempt.attempt} className="mb-1 text-xs text-ink-muted last:mb-0">
                    <span className="font-medium text-ink">
                      Attempt {attempt.attempt}: {statusLabel(attempt.status)}
                    </span>
                    {attempt.reason ? ` — ${attempt.reason}` : ""}
                    {attempt.error ? ` (${attempt.error})` : ""}
                    {attempt.source_url ? (
                      <span className="block break-all text-ink-faint">{attempt.source_url}</span>
                    ) : null}
                  </li>
                ))}
              </ol>
            )}
          </>
        )}
      </div>
      <RunStatusIcon status={track.status} />
    </li>
  );
}

function tagClass(resolution: string, status: HistoryTrack["status"]): string {
  if (resolution === "local" || resolution === "downloaded") {
    return "border-success-border bg-success-subtle text-success-ink";
  }
  if (resolution === "ambiguous" || status === "skipped") {
    return "border-warning-border bg-warning-subtle text-warning-ink";
  }
  if (status === "failed" || status === "cancelled") {
    return "border-danger-border bg-danger-subtle text-danger-ink";
  }
  return "border-line bg-surface-subtle text-ink-muted";
}
