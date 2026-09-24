/**
 * Flask is reached through same-origin paths rather than an absolute URL, so
 * the development proxy and the packaged application can each route ``/api``
 * to the backend without frontend code knowing the host or port.
 */
export const API_PREFIX = "/api";

export function apiUrl(path: string): string {
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${API_PREFIX}${suffix}`;
}
