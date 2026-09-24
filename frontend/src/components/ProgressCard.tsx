import type { ReactNode } from "react";

/**
 * The shared live-progress panel used by both processing screens, migrated
 * from the Flask ``.progress`` markup: the status line, the progress bar with
 * its remaining-time chip, the Searched / Completed / Successful / Failed
 * stats, the current track line, and any job-level error. The track list (or
 * the batch's playlist panels) is passed through as children.
 */
export function ProgressCard({
  statusLine,
  percent,
  searched,
  resolved,
  completed,
  total,
  successful,
  failed,
  eta,
  currentTrack,
  error,
  children,
}: {
  statusLine: string;
  percent: number;
  searched: number;
  resolved: number;
  completed: number;
  total: number;
  successful: number;
  failed: number;
  /** Null hides the remaining-time chip (nothing to estimate yet). */
  eta?: string | null;
  currentTrack?: string;
  error?: string | null;
  children?: ReactNode;
}) {
  const stageWidth = (value: number) => `${Math.min(100, (value / (total || 1)) * 100 / 3)}%`;
  return (
    <section className="mb-6 flex flex-col gap-3 rounded-lg border border-line bg-surface p-4">
      <p className="m-0 text-md font-semibold">{statusLine}</p>
      <div className="flex items-center gap-3">
        <div
          className="h-2 flex-1 overflow-hidden rounded-sm bg-track"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(percent * 100)}
        >
          <div className="flex h-full gap-px">
            <div
              className="h-full bg-accent transition-[width] duration-300"
              style={{ width: stageWidth(searched) }}
              title="Search progress"
            />
            <div
              className="h-full bg-accent/80 transition-[width] duration-300"
              style={{ width: stageWidth(resolved) }}
              title="Audio resolution progress"
            />
            <div
              className="h-full rounded-r-sm bg-success transition-[width] duration-300"
              style={{
                width: stageWidth(completed),
                background: percent >= 1 ? "var(--spot-success)" : undefined,
              }}
              title="Metadata and finalization progress"
            />
          </div>
        </div>
        {eta && (
          <span
            aria-live="polite"
            className="flex-none rounded-sm bg-accent-subtle px-2 py-1 text-xs font-semibold whitespace-nowrap text-accent-ink"
          >
            {eta}
          </span>
        )}
      </div>
      <dl className="m-0 grid grid-cols-[repeat(auto-fit,minmax(9rem,1fr))] gap-4">
        <div>
          <dt className="text-xs font-medium tracking-[0.04em] text-ink-muted uppercase">Searched</dt>
          <dd className="mt-1 m-0 text-lg font-semibold">
            {fmt(searched)} / {fmt(total)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium tracking-[0.04em] text-ink-muted uppercase">Audio ready</dt>
          <dd className="mt-1 m-0 text-lg font-semibold">
            {fmt(resolved)} / {fmt(total)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium tracking-[0.04em] text-ink-muted uppercase">Completed</dt>
          <dd className="mt-1 m-0 text-lg font-semibold">
            {fmt(completed)} / {fmt(total)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium tracking-[0.04em] text-ink-muted uppercase">Successful</dt>
          <dd className="mt-1 m-0 text-lg font-semibold">{fmt(successful)}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium tracking-[0.04em] text-ink-muted uppercase">Failed</dt>
          <dd className="mt-1 m-0 text-lg font-semibold">{fmt(failed)}</dd>
        </div>
      </dl>
      {currentTrack && <p className="m-0 text-sm text-ink-muted italic">{currentTrack}</p>}
      {error && (
        <p role="alert" className="m-0 text-sm text-danger-ink">
          {error}
        </p>
      )}
      {children}
    </section>
  );
}

export function fmt(value: number): string {
  return value.toLocaleString();
}