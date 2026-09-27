import { Minus, Square, Copy, X } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { getBridge, hasWindowControls, windowPlatform } from "../lib/bridge";
import type { Bridge } from "../lib/bridge";
import { SHELL_STORAGE_KEY, frameChrome, useShell } from "./Shell";

/**
 * Custom title bar for the frameless native window, migrated from
 * ``_titlebar.html``. The bar is rendered only once the pywebview bridge has
 * revealed the desktop shell (a browser run keeps its normal page), and the
 * ``.pywebview-drag-region`` element is what pywebview uses to drag a frameless
 * window.
 *
 * On Windows and Linux this bar draws the window controls. On macOS the native
 * AppKit traffic lights are restored (see ``desktop_macos.py``), so the bar is
 * only a transparent drag strip and the controls are hidden. macOS's green
 * control is full screen; Windows and Linux maximize and restore within the
 * current desktop. Which of the two applies is decided by the platform stamped
 * into the bundle at build time, not read once when this effect happens to run.
 */

export function TitleBar() {
  const { framed, platform, maximized, setStatus } = useShell();
  const apiRef = useRef<Bridge | null>(null);

  const chrome = useMemo(() => frameChrome(platform), [platform]);
  const isMac = chrome.macStrip;

  useEffect(() => {
    // Registered before the handshake is awaited, and re-registered on it, so a
    // maximize/restore the OS pushes while the page is still loading reaches
    // the icon instead of being dropped on a hook that did not exist yet. The
    // hook is kept for the life of the window rather than removed on cleanup.
    const registerTitlebarBridge = () => {
      window.spotm3uTitlebar = {
        setMaximized: (value) => setStatus({ maximized: Boolean(value) }),
      };
    };

    const activate = (api: Bridge) => {
      if (!hasWindowControls()) return;
      apiRef.current = api;
      registerTitlebarBridge();
      setStatus({ framed: true, platform: windowPlatform() });
      try {
        // Only the frame flag is remembered. The platform is stamped into the
        // bundle, so writing it here would persist a value a first render may
        // have read before the bridge existed, and every later read would
        // reproduce it.
        sessionStorage.setItem(SHELL_STORAGE_KEY, "1");
      } catch {
        // sessionStorage can be unavailable; the frame still appears once the
        // bridge handshake completes.
      }
      api.is_maximized()
        .then((value) => setStatus({ maximized: Boolean(value) }))
        .catch(() => undefined);
    };

    const ready = () => {
      const api = getBridge();
      if (api) activate(api);
    };

    registerTitlebarBridge();
    // The Python bridge uses this hook to push OS-driven maximize/restore
    // changes (window snapping, native gestures) into the icon, not just our
    // buttons.
    window.addEventListener("pywebviewready", ready);

    // Session flag so reloads draw the frame immediately; the bridge handshake
    // may still be pending on the very first load of a fresh desktop window.
    let stored: string | null = null;
    try {
      stored = sessionStorage.getItem(SHELL_STORAGE_KEY);
    } catch {
      // sessionStorage can be unavailable; the frame appears once the bridge
      // handshake completes.
    }
    if (stored !== null && hasWindowControls()) {
      setStatus({ framed: true, platform: windowPlatform() });
      return () => window.removeEventListener("pywebviewready", ready);
    }
    const api = getBridge();
    if (api && hasWindowControls()) {
      activate(api);
      return () => window.removeEventListener("pywebviewready", ready);
    }
    return () => window.removeEventListener("pywebviewready", ready);
  }, [setStatus]);

  if (!framed || (isMac && maximized)) return null;

  const maximizeLabel = maximized ? "Restore window" : "Maximize window";

  const run = async (action: "minimize" | "maximize" | "close") => {
    const api = apiRef.current;
    if (!api) return;
    try {
      if (action === "minimize") await api.minimize();
      else if (action === "close") await api.close();
      else setStatus({ maximized: Boolean(await api.toggle_maximize()) });
    } catch (error) {
      console.error("window action failed", action, error);
    }
  };

  return (
    <div
      data-titlebar
      data-platform={platform ?? undefined}
      className={`fixed inset-x-0 top-0 z-[200] ${
        isMac
          ? "h-6 bg-transparent"
          : "flex h-[var(--spot-titlebar-h)] items-center border-b border-line bg-surface"
      }`}
    >
      <div
        className="pywebview-drag-region flex h-full flex-1 min-w-0 cursor-default select-none items-center"
        onDoubleClick={() => {
          run("maximize");
        }}
      >
        {isMac ? null : (
          <span className="inline-flex items-center gap-2 pl-4">
            <img src="/api/icon.png" alt="" width="20" height="20" className="rounded-[0.25rem]" />
            <span className="text-sm font-semibold tracking-[0.02em] text-ink-muted">SpotM3U</span>
          </span>
        )}
      </div>
      {chrome.windowControls && (
        <div className="pywebview-no-drag flex h-full items-center" role="group" aria-label="Window controls">
          <button
            type="button"
            aria-label="Minimize window"
            title="Minimize"
            onClick={() => run("minimize")}
            className="inline-flex size-7 cursor-pointer items-center justify-center rounded-[0.375rem] p-0 text-ink-muted transition-colors hover:bg-state-hover hover:text-ink active:bg-state-active"
          >
            <Minus aria-hidden="true" className="size-[0.95rem]" />
          </button>
          <button
            type="button"
            aria-label={maximizeLabel}
            title={maximizeLabel}
            onClick={() => run("maximize")}
            className="inline-flex size-7 cursor-pointer items-center justify-center rounded-[0.375rem] p-0 text-ink-muted transition-colors hover:bg-state-hover hover:text-ink active:bg-state-active"
          >
            {maximized ? (
              <Copy aria-hidden="true" className="size-[0.95rem]" />
            ) : (
              <Square aria-hidden="true" className="size-[0.95rem]" />
            )}
          </button>
          <button
            type="button"
            aria-label="Close window"
            title="Close"
            onClick={() => run("close")}
            className="mr-1 inline-flex size-7 cursor-pointer items-center justify-center rounded-[0.375rem] p-0 text-ink-muted transition-colors hover:bg-danger hover:text-on-accent active:bg-danger-active"
          >
            <X aria-hidden="true" className="size-[0.95rem]" />
          </button>
        </div>
      )}
    </div>
  );
}