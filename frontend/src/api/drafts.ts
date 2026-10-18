// Client for /api/drafts (src/energy/api/routers/drafts.py): a file uploaded
// to be loaded, while it is checked. The checks and their messages are the
// server's; the app shows what it is told.
import type { AuthClient } from "../auth/authClient";
import { type Api, ApiError } from "./client";
import { type SendProgress, sendFile } from "./upload";

export type Severity = "error" | "warning" | "info";

export interface RuleCount {
  rule: string;
  severity: Severity;
  /** The column's key; null for the file as a whole. */
  column: string | null;
  /** Every row that broke the rule, exactly. */
  rows: number;
  /** How many of those are in the issues list. */
  listed: number;
}

export interface DraftValidation {
  validated_at: string;
  sheet: string;
  header_row: number;
  rows_read: number;
  rows_with_errors: number;
  rows_with_warnings: number;
  blocking: boolean;
  counts: RuleCount[];
}

export interface Draft {
  draft_id: string;
  dataset_key: string;
  status: "receiving" | "uploaded" | "validated" | "failed";
  filename: string;
  declared_bytes: number;
  size_bytes: number | null;
  content_hash: string | null;
  created_at: string;
  uploaded_at: string | null;
  expires_at: string;
  failure_code: string | null;
  validation: DraftValidation | null;
}

export interface Issue {
  /** The Excel row; null for the file as a whole. */
  row: number | null;
  /** The Excel column letter; null when not in one column. */
  column: string | null;
  column_key: string | null;
  rule: string;
  severity: Severity;
  value: string | null;
  params: Record<string, string | number | string[]>;
}

export interface IssuesPage {
  total: number;
  offset: number;
  limit: number;
  items: Issue[];
}

export interface IssueQuery {
  severity?: Severity;
  rule?: string;
  column?: string;
  offset: number;
  limit: number;
}

/** The address of a draft's issues, for a query. */
export function issuesPath(draftId: string, query: IssueQuery): string {
  const search = new URLSearchParams();
  if (query.severity) search.set("severity", query.severity);
  if (query.rule) search.set("rule", query.rule);
  if (query.column) search.set("column", query.column);
  search.set("offset", String(query.offset));
  search.set("limit", String(query.limit));
  return `/drafts/${draftId}/issues?${search.toString()}`;
}

export function fetchDraft(api: Api, draftId: string, signal?: AbortSignal): Promise<Draft> {
  return api.get<Draft>(`/drafts/${draftId}`, signal);
}

export function validateDraft(api: Api, draftId: string): Promise<Draft> {
  return api.post<Draft>(`/drafts/${draftId}/validate`);
}

export function fetchIssues(
  api: Api,
  draftId: string,
  query: IssueQuery,
  signal?: AbortSignal,
): Promise<IssuesPage> {
  return api.get<IssuesPage>(issuesPath(draftId, query), signal);
}

/**
 * Upload a file as a new draft of a dataset. The token is asked for just
 * before sending; the server checks it once, before it reads the file, so a
 * long upload outlives it.
 */
export async function uploadDraft(
  auth: AuthClient,
  datasetKey: string,
  file: File,
  onProgress: (progress: SendProgress) => void,
  signal: AbortSignal,
): Promise<Draft> {
  const token = await auth.getApiToken();
  const answer = await sendFile({
    url: `/api/drafts?dataset_key=${encodeURIComponent(datasetKey)}`,
    file,
    token,
    onProgress,
    signal,
  });
  if (answer.status === 200 || answer.status === 201) {
    return answer.body as Draft;
  }
  const body = answer.body;
  const code =
    typeof body === "object" && body !== null && "code" in body && typeof body.code === "string"
      ? body.code
      : null;
  throw new ApiError(answer.status, code);
}
