import type { ReactNode } from "react";

/**
 * The two inline note patterns the pages use, migrated from ``style.css``:
 * ``.error`` (a danger-tinted bordered block, ``role="alert"``) and
 * ``.page-note`` (a neutral surfaced note for status messages). Field help is
 * the muted one-line hint that accompanies a control.
 */

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="mt-4 mb-4 rounded-sm border border-danger-border bg-danger-subtle px-3 py-2 text-sm font-medium text-danger-ink">
      {children}
    </p>
  );
}

export function StatusNote({ children }: { children: ReactNode }) {
  return (
    <p role="status" className="mt-4 mb-4 rounded-sm border border-line bg-surface-subtle px-4 py-3 text-sm text-ink-muted">
      {children}
    </p>
  );
}

export function FieldHelp({ children }: { children: ReactNode }) {
  return <p className="mt-3 m-0 text-sm text-ink-muted">{children}</p>;
}