import { useEffect, useState } from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "spotm3u-theme";

/** The theme, resolved the same way the Flask pages resolve it: an explicit
 *  choice wins, otherwise the OS setting. */
export function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // Some embedded WebViews can deny storage before their data directory is ready.
  }
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

export function useTheme(): { theme: Theme; toggle(): void } {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    applyTheme(theme);
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // Theme changes still apply for this session when persistent storage is unavailable.
    }
  }, [theme]);

  return {
    theme,
    toggle: () => setTheme((current) => (current === "dark" ? "light" : "dark")),
  };
}