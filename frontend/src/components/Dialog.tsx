import { useEffect, useId, useRef } from "react";

/**
 * The modal pattern, migrated from ``_save_m3u.html`` and the ``.app-dialog``
 * CSS: a compact card, sized against the window rather than its own content,
 * so it never grows into a full-page card. The native ``<dialog>`` supplies
 * the desktop behaviour — showModal traps Tab, Escape returns "cancel", the
 * primary action is focused on open, and the backdrop is the scrim. The
 * grouped detail is the part that gives way in a short window, so the actions
 * below it are always reachable.
 */

export type DialogResult = "confirm" | "cancel";

type AppDialogProps = {
  open: boolean;
  onResult: (result: DialogResult) => void;
  title: string;
  summary?: string;
  groupLabel?: string;
  items?: string[];
  confirmLabel?: string;
  cancelLabel?: string;
};

export function AppDialog({
  open,
  onResult,
  title,
  summary,
  groupLabel,
  items,
  confirmLabel = "Save anyway",
  cancelLabel = "Cancel",
}: AppDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const summaryId = useId();
  const groupId = useId();
  const onResultRef = useRef(onResult);
  onResultRef.current = onResult;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      aria-describedby={summary ? summaryId : undefined}
      className="m-auto w-[min(26rem,calc(100vw-2rem))] max-w-[calc(100vw-2rem)] max-h-[calc(100vh-2rem)] overflow-hidden rounded-lg border border-line bg-surface p-0 text-ink shadow-popover [&::backdrop]:bg-scrim [&[open]]:flex [&[open]]:flex-col"
      onClose={(event) => {
        const value = (event.currentTarget as HTMLDialogElement).returnValue;
        onResultRef.current(value === "confirm" ? "confirm" : "cancel");
      }}
      onClick={(event) => {
        // A click on the backdrop is the usual dismiss gesture; only a click
        // outside the card itself lands here.
        if (event.target === dialogRef.current) {
          dialogRef.current?.close("cancel");
        }
      }}
    >
      <form method="dialog" className="flex min-h-0 flex-col gap-4 m-0 overflow-auto p-5">
        <h2 id={titleId} className="m-0 text-lg">
          {title}
        </h2>
        {summary && (
          <p id={summaryId} className="m-0 text-md text-ink-muted">
            {summary}
          </p>
        )}
        {groupLabel && (
          <div className="min-h-0 flex-[0_1_auto] overflow-auto rounded-md border border-line p-3">
            <p className="m-0 mb-2 text-xs font-semibold tracking-[0.08em] text-ink-faint uppercase">
              {groupLabel}
            </p>
            <ul id={groupId} className="m-0 flex list-none flex-col gap-1 p-0 text-sm text-ink-muted">
              {(items ?? []).map((item) => (
                <li key={item} className="[overflow-wrap:anywhere]">
                  {item}
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="flex flex-none justify-end gap-2">
          <button
            type="submit"
            value="cancel"
            className="inline-flex min-h-[2.375rem] items-center justify-center gap-2 rounded-[0.375rem] border border-line bg-muted-button px-4 py-3 text-md font-medium leading-none text-ink transition-colors hover:border-line-strong hover:bg-state-hover active:bg-state-active"
          >
            {cancelLabel}
          </button>
          <button
            type="submit"
            value="confirm"
            autoFocus={open}
            className="inline-flex min-h-[2.375rem] items-center justify-center gap-2 rounded-[0.375rem] bg-accent px-4 py-3 text-md font-semibold leading-none text-on-accent transition-colors hover:bg-accent-hover active:bg-accent-active"
          >
            {confirmLabel}
          </button>
        </div>
      </form>
    </dialog>
  );
}