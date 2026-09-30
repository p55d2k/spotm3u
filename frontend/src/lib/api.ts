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
  | "cancelled"
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

export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

/**
 * Where one conversion sits in the download queue. Distinct from `JobStatus`:
 * `active` means it holds one of the bounded number of queue slots and is
 * working, `queued` means it has been accepted and is waiting its turn.
 */
export type QueueState = "queued" | "active" | "completed" | "failed" | "cancelled";

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
  /** How many times this track has been sent through the pipeline. */
  attempts: number;
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
  resolved: number;
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
  /** Where this conversion is in the queue, when the app has one. */
  queue_state?: QueueState | null;
  /** A waiting conversion's place in line, counted from one. */
  queue_position?: number | null;
};

/** One conversion as the queue holds it, whether or not it has started. */
export type QueueEntry = {
  job_id: string;
  playlist_id: string;
  playlist_name: string;
  state: QueueState;
  total_tracks: number;
  position: number | null;
  queued_at: number;
  started_at: number | null;
  finished_at: number | null;
  error: string;
};

/** The whole download queue: the counts a header needs and every entry. */
export type QueueSnapshot = {
  max_active: number;
  active: number;
  waiting: number;
  completed: number;
  failed: number;
  cancelled: number;
  total: number;
  entries: QueueEntry[];
};

/** The aggregated progress of every job in the current selection. */
export type BatchState = {
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  completed: number;
  searched: number;
  resolved: number;
  total: number;
  successful: number;
  failed: number;
  stale_outputs: number;
  started_at: number | null;
  playlists: JobState[];
  /** Every conversion the app knows about, in the order the user gave them. */
  queue?: QueueSnapshot | null;
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

/** The UI preferences the application stores for the user (the theme). */
export type Preferences = {
  theme?: "light" | "dark";
};

/**
 * The stored state of a conversion or of one of its tracks. The backend owns
 * these names (``spotm3u/history.py``), and it sends the list it knows alongside
 * the runs so the filter cannot offer a state the model does not have.
 */
export type HistoryStatus =
  | "queued"
  | "processing"
  | "completed"
  | "failed"
  | "cancelled"
  | "skipped";

/** How the stored runs are ordered. */
export type HistoryOrder = "recent" | "oldest" | "name";

/** How many tracks of a stored run are in each state. */
export type HistoryCounts = Record<HistoryStatus, number>;

/** One stored conversion, without its tracks. */
export type HistoryRunSummary = {
  id: number;
  job_id: string;
  playlist_id: string;
  playlist_name: string;
  status: HistoryStatus;
  total_tracks: number;
  counts: HistoryCounts;
  error: string;
  m3u_path: string | null;
  output_dir: string;
  fast_mode: boolean;
  started_at: number | null;
  finished_at: number | null;
};

/**
 * One attempt at one track, as it was recorded. A retry adds an attempt rather
 * than replacing the last one, so this is the history of a track: the first
 * entry is the first conversion, the last is what the track is now.
 */
export type HistoryAttempt = {
  attempt: number;
  status: HistoryStatus;
  stage: string;
  resolution: string;
  reason: string;
  error: string;
  source_url: string | null;
  output_path: string | null;
  queued_at: number | null;
  started_at: number | null;
  finished_at: number | null;
};

/** One track of a stored conversion, as it was left behind. */
export type HistoryTrack = {
  position: number;
  title: string;
  artists: string;
  album: string | null;
  spotify_id: string | null;
  status: HistoryStatus;
  stage: string;
  resolution: string;
  reason: string;
  error: string;
  source_url: string | null;
  output_path: string | null;
  retry_count: number;
  cancelled: boolean;
  queued_at: number | null;
  started_at: number | null;
  finished_at: number | null;
  /** A completed track whose audio file was deleted by hand. */
  file_missing?: boolean;
  /** Every attempt at this track, oldest first; the last one is current. */
  attempts: HistoryAttempt[];
};

/** One stored conversion with its tracks, in playlist order. */
export type HistoryRun = HistoryRunSummary & { tracks: HistoryTrack[] };

/** One page of the stored conversions, with the states that can be filtered. */
export type HistoryPage = {
  runs: HistoryRunSummary[];
  total: number;
  limit: number;
  offset: number;
  order: HistoryOrder;
  statuses: HistoryStatus[];
};

/**
 * The parts of the stored library the developer panel can delete, each on its
 * own. The names are the API's own (``docs/api.md``); the panel adds the labels.
 */
export type StorageItemName =
  | "songs"
  | "playlists"
  | "manifest"
  | "artwork"
  | "lyrics"
  | "uploads";

/** What one deletable part holds right now. */
export type StorageItem = {
  name: StorageItemName;
  files: number;
  bytes: number;
};

/** The inventory the developer panel draws its checkboxes from. */
export type StorageInventory = {
  download_dir: string;
  confirm_phrase: string;
  items: StorageItem[];
};

/** What a clear actually deleted, per item. */
export type ClearReport = {
  download_dir: string;
  removed: Partial<Record<StorageItemName, number>>;
  bytes_freed: number;
};

export function getMeta(): Promise<MetaResponse> {
  return request("/meta");
}

/** The stored UI preferences; empty when nothing has been chosen yet. */
export function getPreferences(): Promise<Preferences> {
  return request("/preferences");
}

/** Store the theme, answering with what is stored afterwards. */
export function savePreferences(theme: "light" | "dark"): Promise<Preferences> {
  return request("/preferences", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ theme }),
  });
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
/**
 * Drop a playlist that is still waiting for a queue slot. Nothing has been
 * downloaded for it, so this never stops work in progress: a playlist that has
 * already started is `409 job_running`.
 */
export function cancelQueuedPlaylist(jobId: string, playlistId: string): Promise<JobState> {
  return request(
    `/jobs/${encodeURIComponent(jobId)}/playlists/${encodeURIComponent(playlistId)}/queue/cancel`,
    { method: "POST" },
  );
}

export function getPlaylistProcessingStatus(jobId: string, playlistId: string): Promise<JobState> {
  return request(
    `/jobs/${encodeURIComponent(jobId)}/playlists/${encodeURIComponent(playlistId)}/processing`,
  );
}

/**
 * The outcome of a retry: the job state as it is now, plus which tracks the
 * retry actually picked up. The tracks are named so the interface can show the
 * rows it is waiting on without having to guess from the counts.
 */
export type RetryResponse = JobState & { retried: number; tracks: number[] };

/**
 * Re-resolve tracks of one playlist that have no usable file on disk. With no
 * `tracks` the backend retries every track that needs it; with them it retries
 * exactly those, which is how one track or a selected set is retried.
 */
export function retryProcessing(
  jobId: string,
  playlistId: string,
  tracks?: readonly number[],
): Promise<RetryResponse> {
  const url = `/jobs/${encodeURIComponent(jobId)}/playlists/${encodeURIComponent(
    playlistId,
  )}/processing/retry`;
  if (!tracks) {
    return request(url, { method: "POST" });
  }
  return request(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tracks }),
  });
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

/** One page of the stored conversions, filtered and ordered by the backend. */
export function getHistory(filters: {
  status?: HistoryStatus | "";
  q?: string;
  order?: HistoryOrder;
  limit?: number;
  offset?: number;
} = {}): Promise<HistoryPage> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.q) params.set("q", filters.q);
  if (filters.order) params.set("order", filters.order);
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  if (filters.offset) params.set("offset", String(filters.offset));
  const query = params.toString();
  return request(`/history${query ? `?${query}` : ""}`);
}

/** One stored conversion with its tracks, annotated with what is on disk. */
export function getHistoryRun(runId: string): Promise<HistoryRun> {
  return request(`/history/${encodeURIComponent(runId)}`);
}

/** What a developer clear would remove right now, per selectable item. */
export function getStorageInventory(): Promise<StorageInventory> {
  return request("/developer/inventory");
}
/**
 * Delete the selected items from disk. ``confirm`` must carry the phrase the
 * inventory reported; the deletion is permanent, so the API requires it of every
 * caller and not only of the panel.
 */
export function clearStoredData(
  items: StorageItemName[],
  confirm: string,
): Promise<ClearReport> {
  return request("/developer/clear", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items, confirm }),
  });
}