import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { CircleCheck, TriangleAlert, XCircle } from "lucide-react";

/**
 * Shared transient feedback, migrated from ``_feedback.html``. One toast at a
 * time, bottom-center, following the same tones and lifespans as the Flask
 * shell: success 5s, warning 12s, error 10s. Errors stay longer than
 * successes because a failure the user might have missed is worse than a
 * stale confirmation. Content is user-facing text; technical detail belongs
 * in the console and the SpotM3U log.
 */

export type ToastTone = "ok" | "warn" | "err";

export type ToastAction = { label: string; onClick: () => void };

export type ToastOptions = { timeout?: number };

type ToastInput = ToastOptions & {
  tone: ToastTone;
  title: string;
  detail?: string;
  action?: ToastAction;
  actions?: ToastAction[];
};

type ToastApi = {
  show(input: ToastInput): void;
  success(title: string, detail?: string, options?: ToastHelperOptions): void;
  warning(title: string, detail?: string, options?: ToastHelperOptions): void;
  error(title: string, detail?: string, options?: ToastHelperOptions): void;
  dismiss(): void;
};

export type ToastHelperOptions = ToastOptions & {
  action?: ToastAction;
  actions?: ToastAction[];
};

const TIMEOUTS: Record<ToastTone, number> = { ok: 5000, warn: 12000, err: 10000 };

const ToastContext = createContext<ToastApi | null>(null);

const TONE_ICONS: Record<ToastTone, typeof CircleCheck> = {
  ok: CircleCheck,
  warn: TriangleAlert,
  err: XCircle,
};

const TONE_CLASSES: Record<ToastTone, { border: string; tile: string }> = {
  ok: { border: "border-success-border", tile: "bg-success-subtle text-success-ink" },
  warn: { border: "border-warning-border", tile: "bg-warning-subtle text-warning-ink" },
  err: { border: "border-danger-border", tile: "bg-danger-subtle text-danger-ink" },
};

const TOAST_ANIMATION = "animate-[toast-in_0.18s_ease]";

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<ToastInput | null>(null);
  const hideTimer = useRef<number | null>(null);

  const dismiss = useCallback(() => {
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    hideTimer.current = null;
    setToast(null);
  }, []);

  const show = useCallback(
    (input: ToastInput) => {
      const delay = input.timeout === undefined ? TIMEOUTS[input.tone] : input.timeout;
      window.clearTimeout(hideTimer.current ?? undefined);
      setToast(input);
      if (delay > 0) {
        hideTimer.current = window.setTimeout(dismiss, delay);
      }
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      show,
      dismiss,
      success: (title, detail, options) =>
        show({ ...options, tone: "ok", title, detail }),
      warning: (title, detail, options) =>
        show({ ...options, tone: "warn", title, detail }),
      error: (title, detail, options) =>
        show({ ...options, tone: "err", title, detail }),
    }),
    [show, dismiss],
  );

  const tone = toast ? TONE_CLASSES[toast.tone] : null;
  const ToneIcon = toast ? TONE_ICONS[toast.tone] : null;
  const actions = toast ? (toast.actions ?? (toast.action ? [toast.action] : [])) : [];

  return (
    <ToastContext.Provider value={api}>
      {children}
      {toast && tone && ToneIcon && (
        <div
          role="status"
          aria-live="polite"
          className={`pointer-events-auto fixed bottom-6 left-1/2 z-[300] flex min-w-64 max-w-[min(30rem,calc(100vw-2rem))] items-center gap-3 rounded-lg border bg-surface p-3 pr-4 shadow-popover ${TOAST_ANIMATION} ${tone.border}`}
        >
          <span
            className={`flex size-7 shrink-0 items-center justify-center rounded-full ${tone.tile}`}
            aria-hidden="true"
          >
            <ToneIcon className="size-4" />
          </span>
          <span className="flex min-w-0 flex-1 flex-col gap-1">
            <strong className="truncate text-sm">{toast.title}</strong>
            {toast.detail && <span className="text-xs text-ink-muted">{toast.detail}</span>}
          </span>
          {actions.map((action) => (
            <button
              key={action.label}
              type="button"
              className="min-h-8 shrink-0 whitespace-nowrap rounded-[0.375rem] border border-line bg-muted-button px-3 text-sm font-medium text-ink transition-colors hover:border-line-strong hover:bg-state-hover active:bg-state-active"
              onClick={() => {
                dismiss();
                action.onClick();
              }}
            >
              {action.label}
            </button>
          ))}
        </div>
      )}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("useToast must be used inside a ToastProvider");
  }
  return context;
}