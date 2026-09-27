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

import { BUILD_TARGET_PLATFORM } from "./target";

export type WindowPlatform = "mac" | "windows" | "linux";

/** pywebview names its WebView backends after the platforms they run on. */
const BRIDGE_PLATFORMS: Readonly<Record<string, WindowPlatform>> = {
  cocoa: "mac",
  edgechromium: "windows",
  mshtml: "windows",
  gtk3: "linux",
  qt: "linux",
  qtwebkit: "linux",
};

const SUPPORTED_PLATFORMS: ReadonlySet<string> = new Set([
  "mac",
  "windows",
  "linux",
]);

function asPlatform(value: string | null | undefined): WindowPlatform | null {
  if (!value) return null;
  return SUPPORTED_PLATFORMS.has(value) ? (value as WindowPlatform) : null;
}

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

/** The platform this bundle was built for, or null when it was not stamped. */
export function buildTargetPlatform(): WindowPlatform | null {
  return asPlatform(BUILD_TARGET_PLATFORM);
}

/** The platform the bridge reports, or null until it reports a usable one. */
export function bridgePlatform(): WindowPlatform | null {
  const backend = window.pywebview?.platform || "";
  return BRIDGE_PLATFORMS[backend] ?? null;
}

/**
 * The platform the window frame is drawn for.
 *
 * The stamped build target is authoritative: the application is built per
 * platform, so this is already known before the bridge handshake completes. The
 * bridge corroborates it and covers a bundle that was built without stamping.
 * An unrecognized value is not a platform -- anything this cannot name is
 * reported as unknown so the frame draws nothing rather than guessing.
 */
export function windowPlatform(): WindowPlatform | null {
  return buildTargetPlatform() ?? bridgePlatform();
}

export function getTitlebarBridge(): TitlebarBridge | null {
  return window.spotm3uTitlebar ?? null;
}