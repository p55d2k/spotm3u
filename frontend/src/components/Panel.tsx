import type { HTMLAttributes, ReactNode } from "react";

/** A surface card, migrated from ``.panel`` in ``style.css``: a filled,
 *  bordered block that separates a unit of content from the page surface. */
export function Panel({ className = "", children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`rounded-md border border-line bg-surface ${className}`} {...props}>
      {children}
    </div>
  );
}

export type EmptyStateProps = {
  title: string;
  text?: ReactNode;
  actions?: ReactNode;
};

/** The empty state, migrated from ``.empty-state``: a plain bordered block
 *  that names what is missing and what to do next. It is intentionally not a
 *  decorated card — a desktop utility explains the absence, it does not
 *  illustrate it. */
export function EmptyState({ title, text, actions }: EmptyStateProps) {
  return (
    <div className="mt-4 max-w-[44rem] rounded-md border border-line bg-surface-subtle p-4">
      <h2 className="m-0 text-base">{title}</h2>
      {text && <p className="mt-2 m-0 text-sm text-ink-muted">{text}</p>}
      {actions && <div className="mt-4 flex flex-wrap items-center gap-3">{actions}</div>}
    </div>
  );
}