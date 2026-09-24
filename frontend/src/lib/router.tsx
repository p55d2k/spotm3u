import { useEffect, useState } from "react";
import type { AnchorHTMLAttributes, MouseEvent } from "react";

/**
 * The application's own hash-free router.
 *
 * Flask and Vite both serve the application at ``/``. Navigation uses the
 * history API so native back/forward gestures keep working in the WebView, and
 * the current route is exposed through ``useRoute``.
 */

/** The absolute address of an internal route in this environment. */
export function appUrl(route: string): string {
  return route.startsWith("/") ? route : `/${route}`;
}

/** The current route (e.g. ``/jobs/123/playlists``). */
export function currentRoute(): string {
  return (window.location.pathname || "/") + window.location.search;
}

type Listener = () => void;

const listeners = new Set<Listener>();

function notify() {
  for (const listener of listeners) listener();
}

window.addEventListener("popstate", notify);

/** Move to an internal route without a page reload. */
export function navigate(route: string): void {
  window.history.pushState({}, "", appUrl(route));
  notify();
}

export function Link({
  to,
  onClick,
  children,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement> & { to: string }) {
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.defaultPrevented) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(to);
    onClick?.(event);
  };
  return (
    <a href={appUrl(to)} onClick={handleClick} {...props}>
      {children}
    </a>
  );
}

/** Re-render on route changes (including the back/forward gestures). */
export function useRoute(): string {
  const [route, setRoute] = useState(currentRoute);
  useEffect(() => {
    const listener: Listener = () => setRoute(currentRoute());
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);
  return route;
}

export type AppRoute =
  | { name: "import" }
  | { name: "playlists"; jobId: string }
  | { name: "processing"; jobId: string; playlistId: string }
  | { name: "result"; jobId: string; playlistId: string; fromBatch?: boolean }
  | { name: "batch-processing"; jobId: string }
  | { name: "batch-result"; jobId: string };

/** Match the path against the application's routes. Unknown paths land on import. */
export function parseRoute(route: string): AppRoute {
  const [path, query = ""] = route.split("?");
  const segments = path.split("/").filter(Boolean);
  if (segments[0] !== "jobs" || segments.length < 2) {
    return { name: "import" };
  }
  const jobId = decodeURIComponent(segments[1]);
  if (segments.length === 2 || !segments[2]) return { name: "import" };
  if (segments[2] === "playlists") {
    if (segments.length === 3) return { name: "playlists", jobId };
    const playlistId = decodeURIComponent(segments[3]);
    if (segments[4] === "processing") return { name: "processing", jobId, playlistId };
    if (segments[4] === "result") {
      return {
        name: "result",
        jobId,
        playlistId,
        fromBatch: new URLSearchParams(query).get("batch") === "1",
      };
    }
    return { name: "import" };
  }
  if (segments[2] === "processing") return { name: "batch-processing", jobId };
  if (segments[2] === "result") return { name: "batch-result", jobId };
  return { name: "import" };
}