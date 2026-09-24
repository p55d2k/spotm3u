/**
 * The typed surface of the Python <-> JavaScript bridge (``WindowControls`` in
 * ``spotm3u/desktop.py``).
 *
 * No native desktop capability is reimplemented here; pywebview already
 * provides the window controls, the OS file/save panels, the clipboard-free
 * installer download and the file reveal. This module only describes what the
 * bridge can do and waits for it to appear, because pywebview injects
 * ``window.pywebview`` after the page has finished loading -- so the bridge is
 * resolved at call time, never imported eagerly.
 */

export type WindowPlatform = "mac" | "windows" | "linux";

export type NativeFileResult = {
  cancelled?: boolean;
  error?: string;
  name?: string;
  path?: string;
};

export type SaveM3uResult = NativeFileResult & {
  confirm_required?: boolean;
  saved?: boolean;
  missing?: number;
  total?: number;
  names?: string[];
};

export type UpdateInfo = {
  update_available: boolean;
  current_version?: string;
  latest_version?: string;
  asset_name?: string;
  asset_url?: string;
  release_url?: string;
};

export type Bridge = {
  minimize(): Promise<void>;
  close(): Promise<void>;
  toggle_maximize(): Promise<boolean>;
  is_maximized(): Promise<boolean>;
  choose_zip(): Promise<NativeFileResult>;
  save_m3u(jobId: string, playlistId: string, confirm: boolean): Promise<SaveM3uResult>;
  download_update(assetUrl: string, filename: string): Promise<NativeFileResult>;
  open_at(path: string): Promise<unknown>;
  open_url(url: string): Promise<{ error?: string }>;
};

/** The ``window.spotm3uTitlebar`` hook the Python bridge calls on OS-driven
 *  maximize/restore changes (window snapping, native gestures). */
export type TitlebarBridge = {
  setMaximized(maximized: boolean): void;
};

type PywebviewWindow = {
  api?: Record<string, unknown>;
  platform?: string;
};

declare global {
  interface Window {
    pywebview?: PywebviewWindow;
    spotm3uTitlebar?: TitlebarBridge;
  }
}

const API_METHODS: ReadonlyArray<keyof Bridge> = [
  "minimize",
  "close",
  "toggle_maximize",
  "is_maximized",
  "choose_zip",
  "save_m3u",
  "download_update",
  "open_at",
  "open_url",
];

/** The desktop bridge, or null in a browser run. */
export function getBridge(): Bridge | null {
  const api = window.pywebview?.api;
  if (!api) return null;
  if (API_METHODS.some((method) => typeof api[method] !== "function")) return null;
  return api as unknown as Bridge;
}

/** The window controls are only guaranteed to exist in the desktop shell. */
export function hasWindowControls(): boolean {
  const api = window.pywebview?.api;
  return Boolean(api && typeof api.minimize === "function" && typeof api.close === "function");
}

/** pywebview names its WebView backends after the platforms they run on. */
export function windowPlatform(): WindowPlatform | null {
  if (!window.pywebview) return null;
  const platform = window.pywebview.platform || "";
  if (platform === "cocoa") return "mac";
  if (platform === "edgechromium" || platform === "mshtml") return "windows";
  return "linux";
}

export function getTitlebarBridge(): TitlebarBridge | null {
  return window.spotm3uTitlebar ?? null;
}