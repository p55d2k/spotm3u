import type { HistoryRunSummary, HistoryStatus } from "./api";

/**
 * The small view helpers the two history pages share: how many runs the list
 * shows before it asks for more, and how a stored timestamp or a run's length
 * reads in the user's own words. The stored values are milliseconds, exactly as
 * the live job snapshots use them, so the pages never invent their own clock.
 */

/** How many runs the list shows at first, and per "Show more". */
export const HISTORY_PAGE_SIZE = 25;

/** The most the API will serve in one answer, and so the most the list can show. */
export const HISTORY_MAX_LIMIT = 500;

/** The runs that are still moving; the list keeps itself fresh while any are. */
export function isLive(status: HistoryStatus): boolean {
  return status === "queued" || status === "processing";
}

/** A stored millisecond timestamp as the user reads it. */
export function formatWhen(stamp: number | null | undefined): string {
  if (!stamp) return "";
  return new Date(stamp).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** How long a run took, from the two stored timestamps. */
export function formatDuration(started: number | null, finished: number | null): string {
  if (!started || !finished) return "";
  const seconds = Math.max(0, Math.round((finished - started) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return seconds % 60 ? `${minutes} min ${seconds % 60} s` : `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return hours === 1 ? `1 h ${minutes % 60} min` : `${hours} h ${minutes % 60} min`;
}

/** How a run's row describes what came of it ("9 of 12 finished, 3 failed"). */
export function runSummary(run: HistoryRunSummary): string {
  const parts = [`${run.counts.completed} of ${run.total_tracks} finished`];
  const failed = run.counts.failed + run.counts.cancelled;
  if (failed) parts.push(`${failed} failed`);
  if (run.counts.skipped) parts.push(`${run.counts.skipped} skipped`);
  if (isLive(run.status)) parts.push("in progress");
  return parts.join(" · ");
}
