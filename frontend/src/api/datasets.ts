// Client for GET /api/datasets (src/energy/api/routers/datasets.py): what the
// app expects of each kind of data people load, and how large a file may be.
// The specs are the server's (config/datasets/*.yaml); the app draws from them
// and holds no rule of its own.
import type { Api, Download } from "./client";

export interface DatasetColumn {
  header: string;
  key: string;
  label: string | null;
  type: "text" | "integer" | "decimal" | "date";
  required: boolean;
  pattern: string | null;
  allowed: string[] | null;
  min: number | null;
  max: number | null;
  severity: "error" | "warning";
}

export interface Dataset {
  key: string;
  kind: "snapshot" | "movement";
  origin: "export" | "catalogue";
  /** A key of es.json: the dataset's name. */
  label: string;
  /** A key of es.json: what the dataset is. */
  description: string;
  entry_modes: ("upload" | "grid" | "form")[];
  sheet: string;
  header_row: number;
  natural_key: string[];
  period_column: string | null;
  columns: DatasetColumn[];
}

export interface UploadLimits {
  /** The largest file the server accepts. */
  max_file_bytes: number;
  /** The largest file the server validates now; a larger one is only kept. */
  validated_in_request_bytes: number;
}

export interface Datasets {
  datasets: Dataset[];
  limits: UploadLimits;
}

export function fetchDatasets(api: Api, signal?: AbortSignal): Promise<Datasets> {
  return api.get<Datasets>("/datasets", signal);
}

export function downloadTemplate(api: Api, key: string): Promise<Download> {
  return api.download(`/datasets/${encodeURIComponent(key)}/template`);
}
