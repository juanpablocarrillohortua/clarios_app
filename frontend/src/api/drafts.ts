// Client for /api/drafts (src/energy/api/routers/drafts.py): a file uploaded
// to be loaded, while it is checked. The checks, their reasons and the Excel
// fixes are the server's; the app shows what it is told. A corrected file is
// uploaded as a correction of the draft it came from, and the server says
// which issues it cleared (ADR 0009: data is corrected in Excel).
import type { AuthClient } from "../auth/authClient";
import { type Api, ApiError } from "./client";
import { type SendProgress, sendFile } from "./upload";

export type Severity = "error" | "warning" | "info";

export type Params = Record<string, string | number | string[]>;

/** A likely reason for an issue: es.json says why, and what to do in Excel. */
export interface Reason {
  reason: string;
  params: Params;
}

export interface ReasonCount {
  reason: string;
  rows: number;
}

export interface RuleCount {
  rule: string;
  severity: Severity;
  /** The column's key; null for the file as a whole. */
  column: string | null;
  /** Every row that broke the rule, exactly. */
  rows: number;
  /** How many of those are in the issues list. */
  listed: number;
  /** The same rows by their first reason, most first. */
  reasons: ReasonCount[];
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

export type ComparisonState =
  | "ready"
  | "not_validated"
  | "previous_not_validated"
  | "previous_gone"
  | "rules_changed";

/** How a correction compares with the file it corrects. */
export interface Comparison {
  state: ComparisonState;
  previous: { draft_id: string; filename: string; validated_at: string | null } | null;
  /** The earlier file was validated again, so both are judged by the same rules. */
  previous_revalidated: boolean;
  cleared: number;
  remaining: number;
  new: number;
  /** Rules and columns with too many issues on both sides to compare one by one. */
  by_count_only: { rule: string; column: string | null; before: number; after: number }[];
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
  /** The draft whose file this one corrects. */
  previous_draft_id: string | null;
  comparison: Comparison | null;
}

export type Change = "new" | "remaining";

export interface Issue {
  /** The Excel row; null for the file as a whole. */
  row: number | null;
  /** The Excel column letter; null when not in one column. */
  column: string | null;
  column_key: string | null;
  rule: string;
  severity: Severity;
  value: string | null;
  params: Params;
  /** Most likely first; never empty. */
  reasons: Reason[];
  /** For a correction: whether the earlier file had it. */
  change: Change | null;
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
  change?: Change;
  offset: number;
  limit: number;
}

/** The address of a draft's issues, for a query. */
export function issuesPath(draftId: string, query: IssueQuery): string {
  const search = new URLSearchParams();
  if (query.severity) search.set("severity", query.severity);
  if (query.rule) search.set("rule", query.rule);
  if (query.column) search.set("column", query.column);
  if (query.change) search.set("change", query.change);
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

/** A page of the issues of the earlier file that a correction no longer has. */
export function fetchCleared(
  api: Api,
  draftId: string,
  offset: number,
  limit: number,
  signal?: AbortSignal,
): Promise<IssuesPage> {
  return api.get<IssuesPage>(`/drafts/${draftId}/cleared?offset=${offset}&limit=${limit}`, signal);
}

/** Where a file goes: a new draft of a dataset, or a correction of a draft. */
export type UploadTarget = { datasetKey: string } | { correcting: string };

function uploadUrl(target: UploadTarget): string {
  return "correcting" in target
    ? `/api/drafts/${encodeURIComponent(target.correcting)}/corrections`
    : `/api/drafts?dataset_key=${encodeURIComponent(target.datasetKey)}`;
}

/**
 * Upload a file as a new draft, or as the correction of one. The token is
 * asked for just before sending; the server checks it once, before it reads
 * the file, so a long upload outlives it.
 */
export async function uploadDraft(
  auth: AuthClient,
  target: UploadTarget,
  file: File,
  onProgress: (progress: SendProgress) => void,
  signal: AbortSignal,
): Promise<Draft> {
  const token = await auth.getApiToken();
  const answer = await sendFile({ url: uploadUrl(target), file, token, onProgress, signal });
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
