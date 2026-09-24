/**
 * The remaining-time estimate the progress screens draw, migrated from the
 * Flask processing pages. It measures actual per-track completion intervals
 * across polls so the ETA adapts to this machine's speed and to how long
 * downloads really take, instead of extrapolating total elapsed time against
 * the bar. Pure frontend display logic - nothing here knows or cares how the
 * backend estimates anything.
 */

export function progressFraction(searched: number, completed: number, total: number): number {
  const base = total || 1;
  if (base <= 0) return 0;
  return Math.min(1, (searched / base) * 0.5 + (completed / base) * 0.5);
}

function secondsToParts(remainingSeconds: number): string[] {
  let rest = Math.max(1, Math.round(remainingSeconds));
  const days = Math.floor(rest / 86400);
  rest %= 86400;
  const hours = Math.floor(rest / 3600);
  rest %= 3600;
  const minutes = Math.floor(rest / 60);
  const seconds = rest % 60;
  const parts: string[] = [];
  if (days) parts.push(`${days}d`);
  if (hours) parts.push(`${hours}h`);
  if (minutes) parts.push(`${minutes}min`);
  if (!parts.length) parts.push(`${seconds}s`);
  return parts;
}

export function formatRemaining(remainingSeconds: number): string {
  return `~ ${secondsToParts(remainingSeconds).join(" ")} remaining`;
}

const SECS_PER_TRACK_BASELINE = 45;

export type EtaSample = { t: number; completed: number };

export class EtaEstimator {
  samples: EtaSample[] = [];
  anchorAt: number | null = null;
  lastCompleted: number | null = null;

  /** Record a completion boundary, anchoring the very first sample. */
  record(completed: number, startedAtMs: number): void {
    const now = Date.now();
    if (this.anchorAt === null) {
      this.anchorAt = startedAtMs > 0 ? startedAtMs : now;
      this.samples.push({ t: this.anchorAt, completed: 0 });
      this.lastCompleted = null;
    }
    if (completed !== this.lastCompleted) {
      this.samples.push({ t: now, completed });
      this.lastCompleted = completed;
    }
  }

  /** Seconds per track from the completion deltas (or the elapsed average). */
  secondsPerTrack(completed: number, elapsedSecs: number): number | null {
    const deltas: number[] = [];
    for (let i = 1; i < this.samples.length; i += 1) {
      const dCompleted = this.samples[i].completed - this.samples[i - 1].completed;
      const dt = (this.samples[i].t - this.samples[i - 1].t) / 1000;
      if (dCompleted > 0 && dt > 0) {
        deltas.push(dt / dCompleted);
      }
    }
    if (deltas.length) {
      // Exponentially weighted average: recent completions influence the
      // estimate more than the first ones, so slow end-of-playlist downloads
      // pull the rate up quickly.
      const alpha = 0.35;
      let ema = deltas[0];
      for (let i = 1; i < deltas.length; i += 1) {
        ema = alpha * deltas[i] + (1 - alpha) * ema;
      }
      return ema;
    }
    if (completed > 0) return elapsedSecs / completed;
    return null;
  }
}

/**
 * The remaining-time string for a state, or "" when there is nothing to
 * estimate yet. ``record`` has already sampled the state, so this is a pure
 * read that never mutates the estimator.
 */
export function remainingLabel(state: {
  status: string;
  started_at: number | null;
  progress_total: number;
  completed: number;
}, estimator: EtaEstimator): string {
  if (state.status !== "running") return "";
  const startedAt = Number(state.started_at);
  if (!Number.isFinite(startedAt) || startedAt <= 0) return "";
  const now = Date.now();
  const elapsedSecs = (now - startedAt) / 1000;
  if (!Number.isFinite(elapsedSecs) || elapsedSecs < 1) return "";
  let secs = estimator.secondsPerTrack(state.completed, elapsedSecs);
  if (secs === null || !Number.isFinite(secs) || secs <= 0) {
    // No completed track to measure yet (the very first ones are still being
    // searched/resolved): use a neutral per-track baseline so the ETA is
    // visible from the start and refines once completions arrive.
    secs = SECS_PER_TRACK_BASELINE;
  }
  // The countdown is frozen while the current track is in flight: the value
  // only moves at a completion boundary, so a slow last track re-estimates in
  // clear steps instead of drifting the ETA upward second by second.
  const remainingCount = state.progress_total - state.completed;
  if (state.progress_total <= 0 || remainingCount <= 0) return "";
  return formatRemaining(secs * remainingCount);
}