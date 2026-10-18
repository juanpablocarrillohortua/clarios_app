// Client for /api/loads (src/energy/api/routers/loads.py): what landed in
// Bronze, whichever way it came, and each load in detail with the file it came
// from. Every load is evidence anyone who loads data may read; the server
// decides which plants a person sees.
import type { Api, Download } from "./client";

export type LoadOrigin = "app" | "cli";
export type LoadState = "pending" | "running" | "completed" | "failed" | "cancelled";

/** A completed load, as a confirmation or a dataset names it. */
export interface LoadReference {
  run_id: string;
  origin: LoadOrigin;
  finished_at: string | null;
  filename: string | null;
  rows: number;
  uploaded_by_name: string | null;
  window_start: string | null;
  window_end: string | null;
}

export interface SheetLoaded {
  name: string;
  dataset_key: string | null;
  /** The rows landed from it, in a load's detail; null in a list. */
  rows: number | null;
}

export interface LoadSummary {
  run_id: string;
  origin: LoadOrigin;
  state: LoadState;
  source_key: string;
  /** The dataset spec, for a load from the web app. */
  dataset_key: string | null;
  sheets: SheetLoaded[];
  filename: string | null;
  rows: number;
  started_at: string;
  finished_at: string | null;
  window_start: string | null;
  window_end: string | null;
  uploaded_by_name: string | null;
  /** The caller loaded it. */
  mine: boolean;
  warnings_accepted: number;
  error: string;
}

export interface Person {
  subject: string;
  name: string | null;
}

export interface LoadsPage {
  total: number;
  offset: number;
  limit: number;
  items: LoadSummary[];
  people: Person[];
}

export interface RunCount {
  rule: string;
  severity: "error" | "warning" | "info";
  column: string | null;
  rows: number;
}

export interface DifferenceCounts {
  base_run_id: string | null;
  added: number;
  changed: number;
  removed: number;
  unchanged: number;
  /** What Silver does with a removed row, from the dataset spec. */
  removed_rows: "kept" | "dropped" | null;
}

export interface LoadDetail extends LoadSummary {
  plant_code: string;
  flow_name: string;
  original: { filename: string; size_bytes: number; content_hash: string } | null;
  validation: {
    validated_at: string | null;
    rows_read: number;
    rows_with_warnings: number;
    counts: RunCount[];
    accepted: RunCount[] | null;
  } | null;
  difference: DifferenceCounts | null;
  draft_id: string | null;
  /** The caller can still open the draft it came from. */
  draft_available: boolean;
}

export type LoadSort = "started_at" | "rows";

export interface LoadQuery {
  dataset?: string;
  state?: LoadState;
  /** "me", or a person's subject. */
  user?: string;
  origin?: LoadOrigin;
  periodFrom?: string;
  periodTo?: string;
  from?: string;
  to?: string;
  sort?: LoadSort;
  order?: "asc" | "desc";
  offset: number;
  limit: number;
}

/** The address of a page of the history, for a query. */
export function loadsPath(query: LoadQuery): string {
  const search = new URLSearchParams();
  if (query.dataset) search.set("dataset", query.dataset);
  if (query.state) search.set("state", query.state);
  if (query.user) search.set("user", query.user);
  if (query.origin) search.set("origin", query.origin);
  if (query.periodFrom) search.set("period_from", query.periodFrom);
  if (query.periodTo) search.set("period_to", query.periodTo);
  if (query.from) search.set("from", query.from);
  if (query.to) search.set("to", query.to);
  if (query.sort) search.set("sort", query.sort);
  if (query.order) search.set("order", query.order);
  search.set("offset", String(query.offset));
  search.set("limit", String(query.limit));
  return `/loads?${search.toString()}`;
}

export function fetchLoads(api: Api, query: LoadQuery, signal?: AbortSignal): Promise<LoadsPage> {
  return api.get<LoadsPage>(loadsPath(query), signal);
}

export function fetchLoad(api: Api, runId: string, signal?: AbortSignal): Promise<LoadDetail> {
  return api.get<LoadDetail>(`/loads/${encodeURIComponent(runId)}`, signal);
}

/** The file a load came from, streamed through the API: never from the archive itself. */
export function downloadOriginal(
  api: Api,
  runId: string,
  onProgress: (received: number, total: number) => void,
  signal?: AbortSignal,
): Promise<Download> {
  return api.download(`/loads/${encodeURIComponent(runId)}/original`, signal, onProgress);
}
