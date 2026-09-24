/**
 * Flask is reached through same-origin paths rather than an absolute URL, so
 * the development proxy and the packaged application can each route ``/api``
 * to the backend without frontend code knowing the host or port.
 *
 * The payload types mirror ``docs/api.md`` and the job snapshots exposed by
 * ``spotm3u/web_jobs.py``. No backend logic lives here: the client only names
 * the endpoints and shapes a typed answer from what the API returns.
 */
export const API_PREFIX = "/api";

export function apiUrl(path: string): string {
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${API_PREFIX}${suffix}`;
}

/** Catch-all fields a JSON error may carry beyond ``error``/``code``. */
export type ApiErrorBody = {
  error: string;
  code: string;
  [key: string]: unknown;
};

/** A failed API call, carrying the user-facing text and the stable code. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly body: ApiErrorBody;

  constructor(status: number, body: ApiErrorBody) {
    super(body.error || `Request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
    this.body = body;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), init);
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const payload = (body ?? {}) as Partial<ApiErrorBody>;
    throw new ApiError(response.status, {
      code: payload.code ?? "unknown",
      error: payload.error ?? `The request failed (${response.status}).`,
      ...(payload ?? {}),
    });
  }
  return body as T;
}

/** One playlist as the import and selection screens need it, without tracks. */
export type PlaylistSummary = {
  id: string;
  name: string;
  url: string;
  track_count: number;
};

/** One imported export: its playlists, the stored selection, and defaults. */
export type JobPayload = {
  job_id: string;
  playlists: PlaylistSummary[];
  selected_playlist_ids: string[];
  download_dir: string;
  defaults: { fast_mode: boolean };
};

/** One track of the export, in playlist order. */
export type ExportTrack = {
  index: number;
  title: string;
  artists: string[];
  album: string | null;
  duration_ms: number | null;
  album_artist: string | null;
  track_number: number | null;
  disc_number: number | null;
  release_year: number | null;
  genre: string | null;
  comments: string[];
  spotify_id: string | null;
  spotify_url: string | null;
};

/** A playlist together with its tracks, in export order. */
export type PlaylistDetail = PlaylistSummary & { tracks: ExportTrack[] };

/** The live, terminal, and retried track states the progress view draws. */
export type TrackProcessingStatus =
  | "queued"
  | "resolving-local"
  | "searching"
  | "searched"
  | "validating-source"
  | "downloading"
  | "validating-audio"
  | "enriching-metadata"
  | "complete"
  | "failed"
  | "ambiguous"
  | "skipped";

/** How a resolved track actually ended up (the resolution column). */
export type TrackResolution =
  | "local"
  | "downloaded"
  | "missing"
  | "ambiguous"
  | "rejected"
  | "failed"
  | "uncertain"
  | "";

export type JobStatus = "queued" | "running" | "completed" | "failed";

/** One row of a job's snapshot, with the per-track flags the pages annotate. */
export type SnapshotTrack = {
  index: number;
  title: string;
  artists: string[];
  status: TrackProcessingStatus;
  reason: string | null;
  local_path: string | null;
  source_url: string | null;
  resolution: TrackResolution;
  stage_started_at: number | null;
  /** Whether cached artwork is available to serve (annotated server-side). */
  artwork?: boolean;
  /** A resolved track whose audio file was deleted by hand. */
  file_missing?: boolean;
  /** How the file stores its embedded lyrics; set only on result pages. */
  lyrics?: "synced" | "plain" | null;
};

/** One ``ProcessingJob.snapshot()``, with the per-track artwork flags. */
export type JobState = {
  job_id: string;
  playlist: { id: string; name: string; total_tracks: number };
  current_track: SnapshotTrack | null;
  completed: number;
  progress_total: number;
  searched: number;
  successful: number;
  failed: number;
  ambiguous: number;
  stale_outputs: number;
  counts: Record<string, number> & {
    total: number;
    successful: number;
    local: number;
    downloaded: number;
    missing: number;
    ambiguous: number;
    rejected: number;
    failed: number;
    uncertain: number;
  };
  status: JobStatus;
  error: string | null;
  output_dir: string;
  fast_mode: boolean;
  m3u_path: string | null;
  tracks: SnapshotTrack[];
  started_at: number | null;
  completed_at: number | null;
};

/** The aggregated progress of every job in the current selection. */
export type BatchState = {
  job_id: string;
  status: "completed" | "running" | "failed";
  completed: number;
  searched: number;
  total: number;
  successful: number;
  failed: number;
  stale_outputs: number;
  started_at: number | null;
  playlists: JobState[];
};

/** The read-only capabilities and defaults the shell needs before an upload. */
export type MetaResponse = {
  version: string;
  media_player_available: boolean;
  library_import_available: boolean;
  library_name: string | null;
  defaults: { fast_mode: boolean };
  limits: {
    max_upload_size: number;
    max_decompressed_size: number;
    max_archive_entries: number;
  };
};

/** The outcome of one playlist, with lyrics and the actions it can take. */
export type PlaylistResult = JobState & {
  m3u_url: string;
  media_player_available: boolean;
  library_import_available: boolean;
  library_name: string | null;
};

/** The outcome of every selected playlist, for the batch result screen. */
export type JobResult = BatchState & {
  media_player_available: boolean;
  library_import_available: boolean;
  library_name: string | null;
};

/** The media player handoff report for one playlist. */
export type MediaPlayerAction = {
  action: string;
  message: string;
  imported: number;
  failed: number;
  skipped: number;
  cancelled: boolean;
  opened: boolean;
  unresolved: number;
  partial: boolean;
};

/** The ``POST .../media-player`` answer for one playlist. */
export type MediaPlayerResponse = JobState & { media_player: MediaPlayerAction };

/** The per-playlist outcome of a whole-batch media player import. */
export type BatchImportEntry = {
  playlist_id: string;
  name: string;
  imported: number;
  skipped: number;
  failed: number;
  cancelled: boolean;
  message: string;
  error: string;
};

export type BatchImportResponse = {
  playlists: BatchImportEntry[];
  imported: number;
  skipped: number;
  failed: number;
  cancelled: number;
  errors: number;
  partial: boolean;
  message: string;
};

export function getMeta(): Promise<MetaResponse> {
  return request("/meta");
}

/** Upload an Exportify ZIP the browser picked (multipart ``file`` part). */
export async function uploadArchive(file: File): Promise<JobPayload> {
  const form = new FormData();
  form.append("file", file);
  return request("/upload", { method: "POST", body: form });
}

/** Import the ZIP the desktop shell picked in the OS dialog (no body). */
export function uploadPicked(): Promise<JobPayload> {
  return request("/upload/picked", { method: "POST" });
}

export function getJob(jobId: string): Promise<JobPayload> {
  return request(`/jobs/${encodeURIComponent(jobId)}`);
}

export function getPlaylist(jobId: string, playlistId: string): Promise<PlaylistDetail> {
  return request(`/jobs/${encodeURIComponent(jobId)}/playlists/${encodeURIComponent(playlistId)}`);
}

export function saveSelection(jobId: string, playlistIds: string[]): Promise<{ playlist_ids: string[] }> {
  return request(`/jobs/${encodeURIComponent(jobId)}/selection`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ playlist_ids: playlistIds }),
  });
}

/** Start converting the selection (or a named set) with the chosen mode. */
export function startProcessing(jobId: string, options: {
  playlist_ids?: string[];
  fast_mode: boolean;
}): Promise<BatchState> {
  return request(`/jobs/${encodeURIComponent(jobId)}/processing`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options),
  });
}

/** Live aggregate progress, ready to poll; scoping by ``?playlist_ids``. */
export function getProcessingStatus(jobId: string, playlistIds?: string[]): Promise<BatchState> {
  const scoped = playlistIds && playlistIds.length ? `?playlist_ids=${playlistIds.join(",")}` : "";
  return request(`/jobs/${encodeURIComponent(jobId)}/processing${scoped}`);
}

/** Live progress for one playlist. */
export function getPlaylistProcessingStatus(jobId: string, playlistId: string): Promise<JobState> {
  return request(
    `/jobs/${encodeURIComponent(jobId)}/playlists/${encodeURIComponent(playlistId)}/processing`,
  );
}

/** Re-resolve the tracks of one playlist that have no usable file on disk. */
export function retryProcessing(jobId: string, playlistId: string): Promise<JobState & { retried: number }> {
  return request(
    `/jobs/${encodeURIComponent(jobId)}/playlists/${encodeURIComponent(playlistId)}/processing/retry`,
    { method: "POST" },
  );
}

export function getPlaylistResult(jobId: string, playlistId: string): Promise<PlaylistResult> {
  return request(
    `/jobs/${encodeURIComponent(jobId)}/playlists/${encodeURIComponent(playlistId)}/result`,
  );
}

export function getJobResult(jobId: string): Promise<JobResult> {
  return request(`/jobs/${encodeURIComponent(jobId)}/result`);
}

export function addPlaylistToMediaPlayer(jobId: string, playlistId: string): Promise<MediaPlayerResponse> {
  return request(
    `/jobs/${encodeURIComponent(jobId)}/playlists/${encodeURIComponent(playlistId)}/media-player`,
    { method: "POST" },
  );
}

export function addBatchToMediaPlayer(jobId: string): Promise<BatchImportResponse> {
  return request(`/jobs/${encodeURIComponent(jobId)}/media-player`, { method: "POST" });
}

export function artworkUrl(jobId: string, playlistId: string, index: number): string {
  return apiUrl(`/jobs/${encodeURIComponent(jobId)}/playlists/${encodeURIComponent(playlistId)}/artwork/${index}`);
}