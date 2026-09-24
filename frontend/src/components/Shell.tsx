import { createContext, useContext } from "react";
import type { WindowPlatform } from "../lib/bridge";

/**
 * The desktop frame's live state, shared across the shell: whether the
 * pywebview bridge has revealed the native title bar, which platform it runs
 * on, and whether the window is maximized (full screen on macOS, restored in
 * place on Windows and Linux). The Flask pages derived this from ``<body>``
 * classes and sibling selectors; React keeps it in one place so the layout
 * stays stable while the native window is resized.
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

export const ShellContext = createContext<ShellContextValue | null>(null);

export function useShell(): ShellContextValue {
  const context = useContext(ShellContext);
  if (!context) {
    throw new Error("useShell must be used inside an AppShell");
  }
  return context;
}