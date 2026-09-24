import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { TitleBar } from "./TitleBar";
import { Sidebar } from "./Sidebar";
import type { WorkflowStage } from "./Sidebar";
import { ShellContext, SHELL_STORAGE_KEY } from "./Shell";
import type { ShellStatus } from "./Shell";
import { windowPlatform } from "../lib/bridge";

/**
 * The application frame, migrated from ``_header.html`` and the ``.app-frame``
 * layout: the native title bar, the sidebar on the left, and one scrolling
 * content region on the right. The layout is the shell's own, not a webpage's:
 * the window never scrolls as a page, the sidebar keeps its width while the
 * window is resized, and the content region is the single scroll container.
 *
 * On the first load of a desktop window the bridge handshake (``pywebviewready``)
 * lands a frame late, so the frame state is also read from session memory to
 * be drawn immediately on reloads and in-app navigations -- the same
 * mechanism the Flask shell uses.
 */
export function AppShell({
  currentStage = 1,
  children,
}: {
  currentStage?: WorkflowStage;
  children: ReactNode;
}) {
  const [status, setStatus] = useState<ShellStatus>(() => {
    let stored: string | null = null;
    try {
      stored = sessionStorage.getItem(SHELL_STORAGE_KEY);
    } catch {
      // sessionStorage can be unavailable; the frame appears once the bridge
      // handshake completes.
    }
    return {
      framed: stored !== null,
      platform: stored ? (stored as "mac" | "windows" | "linux") : windowPlatform(),
      maximized: false,
    };
  });

  const value = useMemo(
    () => ({
      ...status,
      setStatus: (patch: Partial<ShellStatus>) => setStatus((current) => ({ ...current, ...patch })),
    }),
    [status],
  );

  // On Windows and Linux the native title bar occupies the top strip, so the
  // content frame clears it. On macOS the traffic lights float over the content
  // and the frame extends right up under them.
  const frameOffset =
    status.framed && status.platform !== "mac" ? "var(--spot-titlebar-h)" : undefined;

  return (
    <ShellContext.Provider value={value}>
      <TitleBar />
      <div className="flex h-full flex-col bg-bg">
        <div
          className="flex min-h-0 flex-1"
          style={frameOffset ? { paddingTop: frameOffset } : undefined}
        >
          <Sidebar currentStage={currentStage} />
          <div className="relative flex min-w-0 flex-1 flex-col overflow-hidden bg-bg">
            <main className="app-scroll flex-1 overflow-y-auto px-6 pt-6 pb-8">
              <div className="mx-auto flex w-full max-w-[var(--spot-content-max)] flex-col">
                {children}
              </div>
            </main>
          </div>
        </div>
      </div>
    </ShellContext.Provider>
  );
}