import { Button } from "./Button";
import { QueueStatusIcon } from "../lib/status";
import type { QueueEntry, QueueSnapshot } from "../lib/api";

/**
 * The queue-aware view of a batch: the counts for the whole queue and one row
 * per conversion, each labelled with what it is actually doing. This replaces
 * the old idea of a single "active playlist", which could not describe several
 * playlists working at once with the rest waiting their turn.
 */

export function QueuePanel({
  queue,
  onCancel,
  cancelling,
}: {
  queue: QueueSnapshot;
  onCancel?: (playlistId: string) => void;
  cancelling?: string | null;
}) {
  return (
    <section
      aria-label="Download queue"
      className="mb-6 flex flex-col gap-3 rounded-lg border border-line bg-surface p-4"
    >
      <p className="m-0 text-md font-semibold">{summary(queue)}</p>
      <ul className="m-0 flex list-none flex-col gap-1 p-0">
        {queue.entries.map((entry) => (
          <QueueRow
            key={`${entry.job_id}:${entry.playlist_id}`}
            entry={entry}
            onCancel={onCancel}
            busy={cancelling === entry.playlist_id}
          />
        ))}
      </ul>
    </section>
  );
}

/** The one line that describes the whole queue, in counts rather than a state. */
function summary(queue: QueueSnapshot): string {
  const parts: string[] = [];
  parts.push(
    `${queue.active} of ${queue.total} converting (limit ${queue.max_active})`,
  );
  if (queue.waiting) parts.push(`${queue.waiting} waiting`);
  if (queue.completed) parts.push(`${queue.completed} finished`);
  if (queue.failed) parts.push(`${queue.failed} failed`);
  if (queue.cancelled) parts.push(`${queue.cancelled} cancelled`);
  return `Download queue — ${parts.join(" · ")}`;
}

function QueueRow({
  entry,
  onCancel,
  busy,
}: {
  entry: QueueEntry;
  onCancel?: (playlistId: string) => void;
  busy: boolean;
}) {
  // A waiting conversion has done nothing yet, so its progress is the honest
  // "not started" rather than a bar at zero that looks stalled.
  const detail =
    entry.state === "queued"
      ? entry.position
        ? `Waiting · position ${entry.position}`
        : "Waiting for a slot"
      : entry.state === "active"
        ? `Converting · ${entry.total_tracks} track${entry.total_tracks === 1 ? "" : "s"}`
        : entry.error || entry.state;
  return (
    <li className="flex items-center gap-2 border-t border-line px-1 py-2 first:border-t-0">
      <QueueStatusIcon state={entry.state} />
      <span className="min-w-0 flex-1 truncate">{entry.playlist_name}</span>
      <span className="flex-none text-sm text-ink-muted">{detail}</span>
      {onCancel && entry.state === "queued" && (
        <Button
          variant="secondary"
          size="small"
          busy={busy}
          onClick={() => onCancel(entry.playlist_id)}
          aria-label={`Remove ${entry.playlist_name} from the queue`}
        >
          Remove
        </Button>
      )}
    </li>
  );
}