import type { AnchorHTMLAttributes, ButtonHTMLAttributes } from "react";
import { Loader2 } from "lucide-react";

/**
 * The button hierarchy, migrated from ``style.css``: one primary (the main
 * action on a screen), secondary (supporting actions that keep their own
 * border), ghost (low-emphasis, lives until pointed at) and danger (for an
 * operation that destroys something). Every variant defines the same states —
 * default, hover, active, focus, disabled, busy — so no screen has to invent
 * one of its own. ``Button`` renders a native button and ``ButtonLink`` an
 * anchor with the same face, matching how the Flask UI draws some actions as
 * links.
 */

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "default" | "small";

const BASE_CLASSES =
  "inline-flex items-center justify-center gap-2 rounded-[0.375rem] text-md leading-none " +
  "transition-colors disabled:cursor-not-allowed disabled:opacity-45";

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-on-accent hover:bg-accent-hover active:bg-accent-active " +
    "disabled:hover:bg-accent",
  secondary:
    "border border-line bg-muted-button font-medium text-ink hover:border-line-strong " +
    "hover:bg-state-hover active:bg-state-active",
  ghost:
    "text-ink-muted font-medium hover:bg-state-hover hover:text-ink active:bg-state-active " +
    "active:text-ink",
  danger: "bg-danger text-on-accent hover:bg-danger-hover active:bg-danger-active",
};

const SIZE_CLASSES: Record<ButtonSize, string> = {
  default: "min-h-[2.375rem] px-4 py-3 font-semibold",
  small: "min-h-8 px-3 py-2 text-sm font-semibold",
};

export function buttonClasses(variant: ButtonVariant = "primary", size: ButtonSize = "default") {
  return `${BASE_CLASSES} ${VARIANT_CLASSES[variant]} ${SIZE_CLASSES[size]}`;
}

type BusyProps = { busy?: boolean };

/** The busy state's spinner, mirrored from the CSV ``::before`` spinner. */
function BusySpinner({ busy }: BusyProps) {
  if (!busy) return null;
  return <Loader2 aria-hidden="true" className="size-3.5 animate-spin" />;
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> &
  BusyProps & {
    variant?: ButtonVariant;
    size?: ButtonSize;
  };

export function Button({ busy, variant = "primary", size = "default", className = "", children, ...props }: ButtonProps) {
  return (
    <button
      aria-busy={busy ? "true" : undefined}
      className={`${buttonClasses(variant, size)} ${className}`}
      {...props}
    >
      <BusySpinner busy={busy} />
      {children}
    </button>
  );
}

type ButtonLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> &
  BusyProps & {
    variant?: ButtonVariant;
    size?: ButtonSize;
  };

export function ButtonLink({
  busy,
  variant = "primary",
  size = "default",
  className = "",
  children,
  ...props
}: ButtonLinkProps) {
  return (
    <a
      aria-busy={busy ? "true" : undefined}
      className={`${buttonClasses(variant, size)} ${className}`}
      {...props}
    >
      <BusySpinner busy={busy} />
      {children}
    </a>
  );
}