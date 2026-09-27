import { Music, RotateCcw } from "lucide-react";
import type { SnapshotTrack } from "../lib/api";
import { artworkUrl } from "../lib/api";
import { statusLabel, TrackStatusIcon } from "../lib/status";
import { Button } from "./Button";

/**
 * One track row of the live progress lists and the result pages, migrated
 * from the Flask markup: playlist position, artwork (or the shared music
 * placeholder), title with the optional lyrics badge, artists, an outcome
 * reason, and the coloured status icon. Tracks keep their playlist order, so
 * the pages render them by index and only re-read the rows they care about.
 *
 * The result pages pass ``retryable`` to offer a retry: either as a checkbox
 * the user can tick for a batch, or as a single button. The row only asks for
 * it, so a track that is already downloaded stays out of the way.
 */
export function TrackRow({
  track,
  jobId,
  playlistId,
  lyricsBadge = false,
  fileMissingReason = false,
  retryable = false,
  selected = false,
  onToggle = undefined,
  onRetry = undefined,
  retryBusy = false,
}: {
  track: SnapshotTrack;
  jobId: string;
  playlistId: string;
  /** Annotate the row with how the file stores its embedded lyrics. */
  lyricsBadge?: boolean;
  /** Replace the generic reason with the "file missing" wording. */
  fileMissingReason?: boolean;
  /** Offer a retry for this track, because it has nothing usable on disk. */
  retryable?: boolean;
  /** Whether the track is ticked for a batch retry. */
  selected?: boolean;
  /** Tick or untick the track for a batch retry. */
  onToggle?: (index: number) => void;
  /** Retry just this track. */
  onRetry?: (index: number) => void;
  /** Whether a retry is running, which only disables the buttons. */
  retryBusy?: boolean;
}) {
  const artists = track.artists.length ? track.artists.join(", ") : "";
  const reason = fileMissingReason && track.file_missing
    ? "File missing from disk — retry to download it again"
    : (track.reason ?? "");
  const resolutionTag = track.resolution ? statusLabel(track.resolution) : null;
  const reasonTags = reason
    ? reason
        .split(";")
        .map((item) => item.trim())
        .filter((item) => item && item.toLowerCase() !== resolutionTag?.toLowerCase())
    : [];

  return (
    <li className="flex items-center gap-3 border-b border-line p-2 last:border-b-0">
      {retryable && onToggle && (
        <input
          type="checkbox"
          className="size-4 flex-none"
          checked={selected}
          onChange={() => onToggle(track.index)}
          aria-label={`Retry ${track.title}`}
        />
      )}
      <span aria-hidden="true" className="w-6 flex-none text-right text-sm text-ink-faint tabular-nums">
        {track.index + 1}
      </span>
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <div
          aria-hidden="true"
          className="flex size-12 flex-none items-center justify-center overflow-hidden rounded-sm bg-accent-subtle text-accent-ink"
        >
          {track.artwork ? (
            <img
              className="block size-12 object-cover"
              src={artworkUrl(jobId, playlistId, track.index)}
              alt=""
              width="48"
              height="48"
              loading="lazy"
              onError={(event) => {
                event.currentTarget.remove();
              }}
            />
          ) : (
            <Music className="size-4" />
          )}
        </div>
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <strong
              className="block min-w-0 truncate text-sm font-medium"
            >
              {track.title}
            </strong>
          </div>
          {artists && (
            <div
              className="truncate text-sm text-ink-muted"
            >
              {artists}
            </div>
          )}
          {(resolutionTag || reasonTags.length > 0 || (lyricsBadge && track.lyrics)) && (
            <div
              className="mt-1 flex min-w-0 items-center gap-1 overflow-x-auto whitespace-nowrap"
              role="group"
              aria-label="Track tags"
            >
              {resolutionTag && (
                <span className={`rounded-sm border px-1.5 py-px text-[0.6875rem] leading-4 font-medium ${resolutionTagClass(track.resolution)}`}>
                  {resolutionTag}
                </span>
              )}
              {reasonTags.map((tag) => (
                <span
                  key={tag}
                  className={`rounded-sm border px-1.5 py-px text-[0.6875rem] leading-4 font-medium ${reasonTagClass(tag)}`}
                >
                  {tag}
                </span>
              ))}
              {lyricsBadge && track.lyrics === "synced" && (
                <span className="rounded-sm border border-success-border bg-success-subtle px-1.5 py-px text-[0.6875rem] leading-4 font-medium text-success-ink">
                  Synced lyrics
                </span>
              )}
              {lyricsBadge && track.lyrics === "plain" && (
                <span className="rounded-sm border border-warning-border bg-warning-subtle px-1.5 py-px text-[0.6875rem] leading-4 font-medium text-warning-ink">
                  Plain lyrics
                </span>
              )}
            </div>
          )}
        </div>
      </div>
      <TrackStatusIcon status={track.resolution || track.status} />
      {retryable && onRetry && (
        <Button
          variant="ghost"
          size="small"
          busy={retryBusy}
          onClick={() => onRetry(track.index)}
          aria-label={`Retry ${track.title}`}
        >
          <RotateCcw className="size-4" aria-hidden="true" />
          Retry
        </Button>
      )}
    </li>
  );
}

function resolutionTagClass(resolution: SnapshotTrack["resolution"]): string {
  switch (resolution) {
    case "local":
      return "border-success-border bg-success-subtle text-success-ink";
    case "downloaded":
      return "border-accent-border bg-accent-subtle text-accent-ink";
    case "ambiguous":
      return "border-warning-border bg-warning-subtle text-warning-ink";
    case "failed":
    case "missing":
    case "rejected":
    case "uncertain":
      return "border-danger-border bg-danger-subtle text-danger-ink";
    default:
      return "border-line bg-surface-subtle text-ink-muted";
  }
}

function reasonTagClass(reason: string): string {
  if (reason.toLowerCase().includes("cached")) {
    return "border-warning-border bg-warning-subtle text-warning-ink";
  }
  if (reason.toLowerCase().includes("missing")) {
    return "border-danger-border bg-danger-subtle text-danger-ink";
  }
  return "border-line-strong bg-surface-subtle text-ink-muted";
}