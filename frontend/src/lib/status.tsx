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

export type StatusInfo = { label: string; group: StatusGroup; icon: LucideIcon };

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

/** The user-facing name of a stage or a job status, in the app's words. */
export function statusLabel(status: string) {
  const entry = TRACK_STATUSES[status];
  if (entry) return entry.label;
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
  const tooltip = useTooltip();
  const entry = TRACK_STATUSES[status] ?? {
    label: statusLabel(status),
    group: "wait" as const,
    icon: CircleHelp,
  };
  const Icon = entry.icon;
  const color = GROUP_COLORS[entry.group];
  return (
    <span
      data-status-icon
      aria-label={entry.label}
      className={`ml-auto inline-flex size-6 flex-none items-center justify-center ${color}`}
      {...tooltip.bind(entry.label)}
    >
      <Icon aria-hidden="true" className="size-4" />
    </span>
  );
}

export type { LucideIcon };