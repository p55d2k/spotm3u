import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { TitleBar } from "./TitleBar";
import { Sidebar } from "./Sidebar";
import type { WorkflowStage } from "./Sidebar";
import { ShellContext, SHELL_STORAGE_KEY, frameChrome } from "./Shell";
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
 * mechanism the Flask shell uses. The platform is not read back: it is stamped
 * into the bundle by the build, so it is known on the very first render.
 */
export function AppShell({
  currentStage = 1,
  children,
}: {
  /** The workflow step to mark, or nothing for a page outside the workflow. */
  currentStage?: WorkflowStage;
  children: ReactNode;
}) {
  const [status, setStatus] = useState<ShellStatus>(() => {
    // Only the frame flag is remembered; the platform comes from the bundle's
    // build stamp, so a value lost to a first render before the bridge existed
    // cannot be read back as a later "known" answer.
    let framed = false;
    try {
      framed = sessionStorage.getItem(SHELL_STORAGE_KEY) !== null;
    } catch {
      // sessionStorage can be unavailable; the frame appears once the bridge
      // handshake completes.
    }
    return {
      framed,
      platform: windowPlatform(),
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
  const chrome = frameChrome(status.platform);
  const frameOffset =
    status.framed && chrome.offsetTitlebar ? "var(--spot-titlebar-h)" : undefined;

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