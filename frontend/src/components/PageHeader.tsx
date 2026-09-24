import type { ReactNode } from "react";
import { ArrowLeft } from "lucide-react";
import type { AnchorHTMLAttributes } from "react";

/**
 * The page-head pattern every screen shares, migrated from ``style.css``: an
 * optional eyebrow, the page title, a muted intro line, and the screen's own
 * actions on the right. It is a region of the shell, not a website hero, so it
 * wraps instead of expanding to the viewport.
 */

type PageHeaderProps = {
  eyebrow?: string;
  title: string;
  intro?: ReactNode;
  actions?: ReactNode;
};

export function PageHeader({ eyebrow, title, intro, actions }: PageHeaderProps) {
  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-6">
      <div className="min-w-0">
        {eyebrow && (
          <p className="mb-2 text-xs font-semibold tracking-[0.06em] text-ink-muted uppercase">
            {eyebrow}
          </p>
        )}
        <h1 className="m-0 text-title leading-[1.3]">{title}</h1>
        {intro && <p className="mt-3 max-w-[52rem] m-0 text-md text-ink-muted">{intro}</p>}
      </div>
      {actions && <div className="shrink-0">{actions}</div>}
    </header>
  );
}

/** The quiet one-level-up link back into a workflow ("Back to playlists"). */
export function BackLink({
  className = "",
  children,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement>) {
  return (
    <a
      className={`mt-2 inline-flex items-center gap-1 text-md font-medium text-ink-muted hover:text-accent-ink hover:no-underline ${className}`}
      {...props}
    >
      <ArrowLeft aria-hidden="true" className="size-3.5" />
      {children}
    </a>
  );
}