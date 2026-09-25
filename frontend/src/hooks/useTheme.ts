import { useEffect, useRef, useState } from "react";

import { getPreferences, savePreferences } from "../lib/api";

export type Theme = "light" | "dark";

const STORAGE_KEY = "spotm3u-theme";

function isTheme(value: string | null | undefined): value is Theme {
  return value === "light" || value === "dark";
}

/** The theme, resolved the way the pages have always resolved it: an explicit
 *  choice wins, otherwise the OS setting.
 *
 *  The explicit choice is read from ``<html data-theme>`` first — the shell
 *  renders the stored preference into the document, and that is the copy which
 *  survives in a WebView that keeps no storage of its own (the macOS desktop
 *  window drops ``localStorage`` between launches). The browser's own copy and
 *  then the OS setting are the fallbacks. */
export function initialTheme(): Theme {
  const rendered = document.documentElement.dataset.theme;
  if (isTheme(rendered)) return rendered;
  const saved = cachedTheme();
  if (saved) return saved;
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

/** Pushes the theme onto ``<html data-theme>``, where every token resolves. */
export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}

function cachedTheme(): Theme | null {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    return isTheme(saved) ? saved : null;
  } catch {
    // Some embedded WebViews can deny storage before their data directory is ready.
    return null;
  }
}

function cacheTheme(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Theme changes still apply for this session when persistent storage is unavailable.
  }
}

export function useTheme(): { theme: Theme; toggle(): void } {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  // The first effect run is the mount rather than a choice: it must not write a
  // preference the user never made.
  const mounted = useRef(false);

  useEffect(() => {
    // The stored preference outlives everything else, so it wins over the copy
    // in this browser. With nothing stored yet, the local choice is pushed up,
    // so a theme picked in a browser run becomes the application's default.
    let cancelled = false;
    getPreferences()
      .then((stored) => {
        if (cancelled) return;
        if (isTheme(stored.theme)) {
          if (stored.theme !== theme) setTheme(stored.theme);
          return;
        }
        const local = cachedTheme();
        if (local) void savePreferences(local).catch(() => {});
      })
      .catch(() => {
        // An unreachable preference is never worth an error; the theme on the
        // page still applies.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    applyTheme(theme);
    cacheTheme(theme);
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    void savePreferences(theme).catch(() => {
      // The theme applies for this session either way; the API reports what was
      // actually stored, so there is nothing to tell the user here.
    });
  }, [theme]);

  return {
    theme,
    toggle: () => setTheme((current) => (current === "dark" ? "light" : "dark")),
  };
}
