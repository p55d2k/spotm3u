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
  | { name: "batch-result"; jobId: string }
  | { name: "history" }
  | { name: "history-run"; runId: string };

/** Match the path against the application's routes. Unknown paths land on import. */
export function parseRoute(route: string): AppRoute {
  const [path, query = ""] = route.split("?");
  const segments = path.split("/").filter(Boolean);
  // The download history is not part of a conversion, so it sits beside the
  // workflow rather than under a job.
  if (segments[0] === "history") {
    if (segments.length === 1) return { name: "history" };
    if (segments.length === 2) {
      return { name: "history-run", runId: decodeURIComponent(segments[1]) };
    }
    return { name: "import" };
  }
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

/** The address a route renders at. */
export function routePath(route: AppRoute): string {
  switch (route.name) {
    case "import":
      return "/";
    case "playlists":
      return `/jobs/${route.jobId}/playlists`;
    case "processing":
      return `/jobs/${route.jobId}/playlists/${route.playlistId}/processing`;
    case "result":
      return `/jobs/${route.jobId}/playlists/${route.playlistId}/result`;
    case "batch-processing":
      return `/jobs/${route.jobId}/processing`;
    case "batch-result":
      return `/jobs/${route.jobId}/result`;
    case "history":
      return "/history";
    case "history-run":
      return `/history/${route.runId}`;
  }
}

// What the history calls the conversion it can send the user back to. Only the
// workflow has a place worth returning to, so import and the history itself are
// left out: the label is the whole point, and "back to where you were" has to
// name the step.
const CONVERSION_LABELS: Record<AppRoute["name"], string | null> = {
  import: null,
  playlists: "Back to choosing playlists",
  processing: "Back to the conversion in progress",
  result: "Back to your results",
  "batch-processing": "Back to the batch in progress",
  "batch-result": "Back to your batch results",
  history: null,
  "history-run": null,
};

const LAST_CONVERSION_KEY = "spotm3u-last-conversion";

/** Somewhere the history can offer to return the user to. */
export type ConversionPlace = { path: string; label: string };

/**
 * A conversion lives entirely in its address: the job, the selection and the
 * playlist are all in the URL, and the import screen knows only how to take a
 * new ZIP. So without a note of where the user was, opening the history costs
 * them the whole job -- they would have to find the back button or upload,
 * select and convert all over again. Session memory is the right scope, like
 * the shell's own state: it lasts as long as the window is open, which is
 * exactly as long as the job does.
 */
export function rememberConversionPlace(route: AppRoute): void {
  const label = CONVERSION_LABELS[route.name];
  if (!label) return;
  try {
    sessionStorage.setItem(LAST_CONVERSION_KEY, JSON.stringify({ path: routePath(route), label }));
  } catch {
    // Session memory can be unavailable; the history then simply offers no way
    // back, which is where it started.
  }
}

/** The last conversion screen visited, or nothing if there has not been one. */
export function lastConversionPlace(): ConversionPlace | null {
  try {
    const stored = sessionStorage.getItem(LAST_CONVERSION_KEY);
    if (!stored) return null;
    const parsed = JSON.parse(stored) as ConversionPlace;
    return typeof parsed?.path === "string" && typeof parsed?.label === "string" ? parsed : null;
  } catch {
    return null;
  }
}
