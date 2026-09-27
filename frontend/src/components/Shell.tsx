import { createContext, useContext } from "react";
import type { WindowPlatform } from "../lib/bridge";

/**
 * The desktop frame's live state, shared across the shell: whether the
 * pywebview bridge has revealed the native title bar, which platform the bundle
 * was built for, and whether the window is maximized (full screen on macOS,
 * restored in place on Windows and Linux). The Flask pages derived this from
 * ``<body>`` classes and sibling selectors; React keeps it in one place so the
 * layout stays stable while the native window is resized.
 *
 * Only ``framed`` is remembered across a reload. The platform is not: it is
 * stamped into the bundle, so persisting a value that a first render may have
 * read before the bridge existed would let a lost value survive every later
 * read and reappear at an arbitrary moment.
 */

export type ShellStatus = {
  framed: boolean;
  platform: WindowPlatform | null;
  maximized: boolean;
};

export type ShellContextValue = ShellStatus & {
  setStatus(patch: Partial<ShellStatus>): void;
};

export const SHELL_STORAGE_KEY = "spotm3u-shell";

/**
 * What the window frame draws, decided once from the platform so the title bar,
 * the application frame and the sidebar cannot disagree. A wrong platform value
 * shows the window controls and drops the traffic light clearance at the same
 * time, which is what makes the defect look like the window changed shape.
 *
 * There is deliberately no platform-conditional CSS: this is the only opinion
 * about which platform the shell is on.
 */
export type FrameChrome = {
  /** Transparent drag strip; the native traffic lights stay visible. */
  macStrip: boolean;
  /** The in-page minimize / maximize / close controls. */
  windowControls: boolean;
  /** The layout clears the title bar height. */
  offsetTitlebar: boolean;
  /** The sidebar reserves the macOS traffic light clearance. */
  trafficLightClearance: boolean;
};

export function frameChrome(platform: WindowPlatform | null): FrameChrome {
  const mac = platform === "mac";
  // Unknown is not a platform, so nothing is drawn for it: no controls, and no
  // clearance reserved either, until the platform is known.
  const known = platform !== null;
  return {
    macStrip: mac,
    windowControls: known && !mac,
    offsetTitlebar: known && !mac,
    trafficLightClearance: mac,
  };
}

export const ShellContext = createContext<ShellContextValue | null>(null);

export function useShell(): ShellContextValue {
  const context = useContext(ShellContext);
  if (!context) {
    throw new Error("useShell must be used inside an AppShell");
  }
  return context;
}