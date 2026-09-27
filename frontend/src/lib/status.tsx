import type { LucideIcon } from "lucide-react";
import {
  AudioLines,
  Ban,
  CircleCheck,
  CircleHelp,
  CircleX,
  Database,
  Download,
  FileX,
  FlaskConical,
  Loader,
  Search,
  ShieldCheck,
  SkipForward,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { useTooltip } from "../components/Tooltip";

/**
 * The track statuses, migrated from ``_status_icons.html``: one entry per
 * status with the user-facing label and the color group it belongs to. The
 * icons are Lucide renderings of the same stroke set the Flask pages used, and
 * the unknown-status fallback humanises a machine name so a new stage can
 * never reach the page without a label.
 */

export type StatusGroup = "wait" | "ok" | "warn" | "err";

export type StatusInfo = { label: string; group: StatusGroup; icon: LucideIcon; spinning?: boolean };

export const TRACK_STATUSES: Record<string, StatusInfo> = {
  queued: { label: "Waiting to start", group: "wait", icon: FlaskConical },
  "resolving-local": { label: "Finding local audio", group: "wait", icon: Database },
  searching: { label: "Searching online", group: "wait", icon: Search },
  searched: { label: "Search complete", group: "wait", icon: CircleCheck },
  "validating-source": { label: "Checking source", group: "wait", icon: ShieldCheck },
  downloading: { label: "Downloading", group: "wait", icon: Download },
  "validating-audio": { label: "Checking downloaded audio", group: "wait", icon: AudioLines },
  "enriching-metadata": {
    label: "Adding tags, artwork and lyrics",
    group: "wait",
    icon: Sparkles,
  },
  complete: { label: "Complete", group: "ok", icon: CircleCheck },
  local: { label: "Local match", group: "ok", icon: CircleCheck },
  downloaded: { label: "Downloaded", group: "ok", icon: Download },
  failed: { label: "Failed", group: "err", icon: CircleX },
  rejected: { label: "Rejected", group: "err", icon: Ban },
  missing: { label: "Missing", group: "err", icon: FileX },
  uncertain: { label: "Uncertain", group: "err", icon: CircleHelp },
  ambiguous: { label: "Ambiguous", group: "warn", icon: TriangleAlert },
  skipped: { label: "Skipped", group: "warn", icon: SkipForward },
};

const JOB_LABELS: Record<string, string> = {
  queued: "Waiting to start",
  running: "Processing in progress",
  completed: "Processing complete",
  failed: "Processing failed",
};

/**
 * The six states a stored run (and each of its tracks) can be in. They reuse
 * the track colours, but they are named after the stored record rather than the
 * live job, so a finished conversion reads as "Finished" instead of "Processing
 * complete" on the history pages.
 */
export const RUN_STATUSES: Record<string, StatusInfo> = {
  queued: { label: "Waiting to start", group: "wait", icon: FlaskConical },
  // The one state that is genuinely working rather than waiting, so it is the
  // one that turns. A spinner on "Waiting to start" would claim work that has
  // not begun.
  processing: { label: "In progress", group: "wait", icon: Loader, spinning: true },
  completed: { label: "Finished", group: "ok", icon: CircleCheck },
  failed: { label: "Failed", group: "err", icon: CircleX },
  cancelled: { label: "Cancelled", group: "warn", icon: Ban },
  skipped: { label: "Skipped", group: "warn", icon: SkipForward },
};

/** The user-facing name of a stage or a job status, in the app's words. */
export function statusLabel(status: string) {
  const entry = TRACK_STATUSES[status];
  if (entry) return entry.label;
  if (RUN_STATUSES[status]) return RUN_STATUSES[status].label;
  if (JOB_LABELS[status]) return JOB_LABELS[status];
  const name = String(status || "").replace(/[-_]+/g, " ");
  return name.charAt(0).toUpperCase() + name.slice(1);
}

const GROUP_COLORS: Record<StatusGroup, string> = {
  wait: "text-ink-faint",
  ok: "text-success-ink",
  warn: "text-warning-ink",
  err: "text-danger-ink",
};

/** The coloured status glyph rows carry, with the shared tooltip text. */
export function TrackStatusIcon({ status }: { status: string }) {
  return <StatusGlyph status={status} entry={TRACK_STATUSES[status]} />;
}

/** The same glyph for one of the six stored run states. */
export function RunStatusIcon({ status }: { status: string }) {
  return <StatusGlyph status={status} entry={RUN_STATUSES[status]} />;
}

function StatusGlyph({ status, entry }: { status: string; entry?: StatusInfo }) {
  const tooltip = useTooltip();
  const info = entry ?? {
    label: statusLabel(status),
    group: "wait" as const,
    icon: CircleHelp,
  };
  const Icon = info.icon;
  const color = GROUP_COLORS[info.group];
  return (
    <span
      data-status-icon
      aria-label={info.label}
      className={`ml-auto inline-flex size-6 flex-none items-center justify-center ${color}`}
      {...tooltip.bind(info.label)}
    >
      <Icon aria-hidden="true" className={`size-4 ${info.spinning ? "animate-spin" : ""}`} />
    </span>
  );
}

export type { LucideIcon };