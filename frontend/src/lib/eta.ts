/**
 * The remaining-time estimate the progress screens draw. It measures
 * per-stage track rates across polls and combines the sequential search stage
 * with the overlapping audio and metadata stages.
 */

export function progressFraction(
  searched: number,
  resolved: number,
  completed: number,
  total: number,
): number {
  const base = total || 1;
  if (base <= 0) return 0;
  return Math.min(1, (searched + resolved + completed) / base / 3);
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

export type EtaSample = {
  t: number;
  searched: number;
  resolved: number;
  completed: number;
};

export class EtaEstimator {
  samples: EtaSample[] = [];
  anchorAt: number | null = null;
  lastCounts: [number, number, number] | null = null;

  /** Record stage progress, anchoring the very first sample. */
  record(searched: number, resolved: number, completed: number, startedAtMs: number): void {
    const now = Date.now();
    if (this.anchorAt === null) {
      this.anchorAt = startedAtMs > 0 ? startedAtMs : now;
      this.samples.push({ t: this.anchorAt, searched: 0, resolved: 0, completed: 0 });
      this.lastCounts = null;
    }
    const counts: [number, number, number] = [searched, resolved, completed];
    if (
      this.lastCounts === null ||
      counts.some((count, index) => count !== this.lastCounts?.[index])
    ) {
      this.samples.push({ t: now, searched, resolved, completed });
      this.lastCounts = counts;
    }
  }

  /** Return an EMA of seconds per track for each pipeline stage. */
  secondsPerTrack(
    searched: number,
    resolved: number,
    completed: number,
    elapsedSecs: number,
  ): [number, number, number] {
    const deltas: [number, number, number][] = [];
    for (let i = 1; i < this.samples.length; i += 1) {
      const dt = (this.samples[i].t - this.samples[i - 1].t) / 1000;
      if (dt > 0) {
        deltas.push(
          [0, 1, 2].map((index) => {
            const key = ["searched", "resolved", "completed"][index] as
              | "searched"
              | "resolved"
              | "completed";
            const delta = this.samples[i][key] - this.samples[i - 1][key];
            return delta > 0 ? dt / delta : 0;
          }) as [number, number, number],
        );
      }
    }
    return [0, 1, 2].map((index) => {
      const stageDeltas = deltas.map((delta) => delta[index]).filter((delta) => delta > 0);
      if (stageDeltas.length) {
        const alpha = 0.35;
        let ema = stageDeltas[0];
        for (let i = 1; i < stageDeltas.length; i += 1) {
          ema = alpha * stageDeltas[i] + (1 - alpha) * ema;
        }
        return ema;
      }
      const count = [searched, resolved, completed][index];
      return count > 0 ? elapsedSecs / count : SECS_PER_TRACK_BASELINE;
    }) as [number, number, number];
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
  searched: number;
  resolved: number;
  completed: number;
}, estimator: EtaEstimator): string {
  if (state.status !== "running") return "";
  const startedAt = Number(state.started_at);
  if (!Number.isFinite(startedAt) || startedAt <= 0) return "";
  const now = Date.now();
  const elapsedSecs = (now - startedAt) / 1000;
  if (!Number.isFinite(elapsedSecs) || elapsedSecs < 1) return "";
  if (state.progress_total <= 0) return "";
  const [searchSecs, audioSecs, metadataSecs] = estimator.secondsPerTrack(
    state.searched,
    state.resolved,
    state.completed,
    elapsedSecs,
  );
  const remainingSearch = Math.max(0, state.progress_total - state.searched) * searchSecs;
  const remainingAudio = Math.max(0, state.progress_total - state.resolved) * audioSecs;
  const remainingMetadata = Math.max(0, state.progress_total - state.completed) * metadataSecs;
  // Search prepares the pipeline first; downloads and metadata overlap after
  // that, so their remaining durations are governed by the slower stage.
  return formatRemaining(remainingSearch + Math.max(remainingAudio, remainingMetadata));
}