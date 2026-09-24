import { Music } from "lucide-react";
import type { SnapshotTrack } from "../lib/api";
import { artworkUrl } from "../lib/api";
import { TrackStatusIcon } from "../lib/status";
import { useTooltip } from "./Tooltip";

/**
 * One track row of the live progress lists and the result pages, migrated
 * from the Flask markup: playlist position, artwork (or the shared music
 * placeholder), title with the optional lyrics badge, artists, an outcome
 * reason, and the coloured status icon. Tracks keep their playlist order, so
 * the pages render them by index and only re-read the rows they care about.
 */
export function TrackRow({
  track,
  jobId,
  playlistId,
  lyricsBadge = false,
  fileMissingReason = false,
}: {
  track: SnapshotTrack;
  jobId: string;
  playlistId: string;
  /** Annotate the row with how the file stores its embedded lyrics. */
  lyricsBadge?: boolean;
  /** Replace the generic reason with the "file missing" wording. */
  fileMissingReason?: boolean;
}) {
  const tooltip = useTooltip();
  const artists = track.artists.length ? track.artists.join(", ") : "";
  const reason = fileMissingReason && track.file_missing
    ? "File missing from disk — retry to download it again"
    : (track.reason ?? "");

  return (
    <li className="flex items-center gap-3 border-b border-line p-2 last:border-b-0">
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
              {...(track.title ? tooltip.bind(track.title) : {})}
            >
              {track.title}
            </strong>
            {lyricsBadge && track.lyrics === "synced" && (
              <span
                title="Timed lyrics are embedded in this file"
                className="flex-none rounded-sm border border-success-border bg-success-subtle px-2 py-1 text-xs font-medium whitespace-nowrap text-success-ink"
              >
                Synced lyrics
              </span>
            )}
            {lyricsBadge && track.lyrics === "plain" && (
              <span
                title="Untimed lyrics are embedded in this file"
                className="flex-none rounded-sm border border-warning-border bg-warning-subtle px-2 py-1 text-xs font-medium whitespace-nowrap text-warning-ink"
              >
                Plain lyrics
              </span>
            )}
          </div>
          {artists && (
            <div
              className="truncate text-sm text-ink-muted"
              {...tooltip.bind(artists)}
            >
              {artists}
            </div>
          )}
          {reason && (
            <div className="mt-1 inline-flex rounded-sm border border-danger-border bg-danger-subtle px-2 py-1 text-sm text-danger-ink">
              {reason}
            </div>
          )}
        </div>
      </div>
      <TrackStatusIcon status={track.resolution || track.status} />
    </li>
  );
}