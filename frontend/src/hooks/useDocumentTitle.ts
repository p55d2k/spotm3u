import { useEffect } from "react";

/** Keeps the window title in sync with the screen currently rendered. */
export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title;
  }, [title]);
}
