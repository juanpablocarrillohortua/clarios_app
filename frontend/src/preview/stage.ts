// The stand-ins of the preview page (preview.html, dev server only): a person
// signed in as an operator, an API that answers from made-up data, and an
// upload that takes a few seconds and can be cancelled. Nothing leaves the
// browser. `?scenario=` chooses what the validation finds:
//
//   broken (default)  a catalogue with an issue of every kind
//   clean             nothing found
//   many              more issues than a page holds
//   large             a file over 5 MB: kept, not validated (501)
//   busy              another validation running (409)
//   refused           the upload refused as too large (413)
//
// and, starting on a validated draft with problems, for its corrected file:
//
//   corrected         some problems cleared, some left, one new
//   same              the same file again (200, the same draft)
//   allFixed          every problem cleared
//   rulesChanged      the rules changed and the earlier file could not follow
//   previousGone      the earlier file is no longer kept
//
// The data are made up; no real workbook is behind them.
import type { Datasets } from "../api/datasets";
import type { Comparison, Draft, Issue, IssuesPage, Reason, RuleCount } from "../api/drafts";
import type { Me } from "../api/me";
import type { AuthClient } from "../auth/authClient";

const MIB = 1024 * 1024;
const DRAFT_ID = "00000000-0000-4000-8000-000000000005";
const CORRECTION_ID = "00000000-0000-4000-8000-000000000006";
const UPLOAD_MS = 4000;

const scenario = new URLSearchParams(window.location.search).get("scenario") ?? "broken";
const correcting = ["corrected", "same", "allFixed", "rulesChanged", "previousGone"].includes(
  scenario,
);

function because(reason: string, params: Reason["params"] = {}): Reason {
  return { reason, params };
}

const me: Me = {
  subject: "00000000-0000-4000-8000-00000000abcd",
  name: "Persona de prueba",
  username: "prueba@example.test",
  roles: ["operator"],
  permissions: ["dashboard:view", "loads:write"],
  plant_scope: ["1515"],
};

const datasets: Datasets = {
  datasets: [
    {
      key: "lineas_produccion",
      kind: "snapshot",
      origin: "catalogue",
      label: "datasets.lineas_produccion.name",
      description: "datasets.lineas_produccion.description",
      entry_modes: ["upload"],
      sheet: "Lineas Produccion",
      header_row: 1,
      natural_key: ["Línea"],
      period_column: null,
      columns: Array.from({ length: 10 }, (_, index) => ({
        header: `C${index}`,
        key: `C${index}`,
        label: null,
        type: "text" as const,
        required: false,
        pattern: null,
        allowed: null,
        min: null,
        max: null,
        severity: "error" as const,
      })),
    },
    {
      key: "qad_facturas",
      kind: "movement",
      origin: "export",
      label: "datasets.qad_facturas.name",
      description: "datasets.qad_facturas.description",
      entry_modes: ["upload"],
      sheet: "Datos",
      header_row: 1,
      natural_key: ["Núm Fact", "Orden", "Línea"],
      period_column: "Fecha Efectiva",
      columns: [],
    },
  ],
  limits: { max_file_bytes: 300 * MIB, validated_in_request_bytes: 5 * MIB },
};

const brokenIssues: Issue[] = [
  {
    row: 1,
    column: "C",
    column_key: "Descripción",
    rule: "schema.header_mismatch",
    severity: "error",
    value: "Descripcion",
    params: { found: "Descripcion", expected: "Descripción" },
    reasons: [
      because("header.near_match", { difference: "accent" }),
      because("default.schema.header_mismatch"),
    ],
    change: null,
  },
  {
    row: 1,
    column: null,
    column_key: "Última Firma Automática",
    rule: "schema.column_missing",
    severity: "warning",
    value: null,
    params: { header: "Última Firma Automática" },
    reasons: [
      because("header.similar", { found: "Ultima Firma Automatica" }),
      because("default.schema.column_missing"),
    ],
    change: null,
  },
  {
    row: 1,
    column: "J",
    column_key: "Comentarios",
    rule: "schema.column_unknown",
    severity: "info",
    value: "Comentarios",
    params: { header: "Comentarios" },
    reasons: [because("default.schema.column_unknown")],
    change: null,
  },
  {
    row: 3,
    column: "A",
    column_key: "Línea",
    rule: "cell.required",
    severity: "error",
    value: null,
    params: {},
    reasons: [because("default.cell.required")],
    change: null,
  },
  {
    row: 4,
    column: "A",
    column_key: "Línea",
    rule: "cell.pattern",
    severity: "error",
    value: "faform02",
    params: { pattern: "^[A-Z0-9]+$" },
    reasons: [because("text.lowercase"), because("default.cell.pattern")],
    change: null,
  },
  {
    row: 6,
    column: "D",
    column_key: "Planificación de Unidades por Hora",
    rule: "cell.type",
    severity: "error",
    value: "1.234,5",
    params: { type: "decimal" },
    reasons: [because("number.text_with_separators"), because("default.cell.type")],
    change: null,
  },
  {
    row: 7,
    column: "D",
    column_key: "Planificación de Unidades por Hora",
    rule: "cell.range",
    severity: "error",
    value: "-5",
    params: { min: "0" },
    reasons: [because("default.cell.range")],
    change: null,
  },
  {
    row: 9,
    column: "I",
    column_key: "Firma Automática",
    rule: "cell.allowed",
    severity: "error",
    value: "si",
    params: { allowed: ["Sí", "No"] },
    reasons: [because("allowed.near_match", { suggestion: "Sí" }), because("default.cell.allowed")],
    change: null,
  },
];

function manyIssues(): Issue[] {
  return Array.from({ length: 1000 }, (_, index) => ({
    row: index + 2,
    column: "B",
    column_key: "Almac",
    rule: "cell.pattern",
    severity: "error" as const,
    value: String(100 + (index % 9)),
    params: { pattern: "^[0-9]{4}$" },
    reasons: [because("code.lost_leading_zeros", { digits: 4 }), because("default.cell.pattern")],
    change: null,
  }));
}

const issues = scenario === "many" ? manyIssues() : scenario === "clean" ? [] : brokenIssues;

// The corrected file: two problems left as they were, one new, the rest gone.
const left = new Set([4, 9]);
const correctionIssues: Issue[] =
  scenario === "allFixed"
    ? []
    : [
        ...brokenIssues
          .filter((issue) => issue.row !== null && left.has(issue.row) && issue.row !== 1)
          .map((issue) => ({ ...issue, change: "remaining" as const })),
        {
          row: 8,
          column: "D",
          column_key: "Planificación de Unidades por Hora",
          rule: "cell.range",
          severity: "error",
          value: "-1",
          params: { min: "0" },
          reasons: [because("default.cell.range")],
          change: "new",
        },
      ];
const clearedIssues = brokenIssues.filter(
  (issue) => !(issue.row !== null && left.has(issue.row) && issue.row !== 1),
);

function countsOf(list: Issue[]): RuleCount[] {
  const tally = new Map<string, RuleCount>();
  for (const issue of list) {
    const key = `${issue.rule}:${issue.column_key ?? ""}`;
    const count = tally.get(key) ?? {
      rule: issue.rule,
      severity: issue.severity,
      column: issue.column_key,
      rows: 0,
      listed: 0,
      reasons: [],
    };
    const rows = scenario === "many" ? 45 : 1;
    count.rows += rows;
    count.listed += 1;
    const first = issue.reasons[0]?.reason ?? `default.${issue.rule}`;
    const byReason = count.reasons.find((each) => each.reason === first);
    if (byReason) byReason.rows += rows;
    else count.reasons.push({ reason: first, rows });
    tally.set(key, count);
  }
  return [...tally.values()];
}

const uploaded: Draft = {
  draft_id: DRAFT_ID,
  dataset_key: "lineas_produccion",
  status: "uploaded",
  filename: "Líneas de producción.xlsx",
  declared_bytes: scenario === "large" ? 240 * MIB : 18_432,
  size_bytes: scenario === "large" ? 240 * MIB : 18_432,
  content_hash: "a".repeat(64),
  created_at: "2026-10-17T10:00:00Z",
  uploaded_at: "2026-10-17T10:00:04Z",
  expires_at: "2026-11-16T10:00:04Z",
  failure_code: null,
  validation: null,
  previous_draft_id: null,
  comparison: null,
};

function validated(list: Issue[] = issues, base: Draft = uploaded): Draft {
  const counts = countsOf(list);
  const errorRows = new Set(
    list.filter((issue) => issue.severity === "error" && issue.row !== 1).map((i) => i.row),
  );
  return {
    ...base,
    status: "validated",
    validation: {
      validated_at: "2026-10-17T10:00:06Z",
      sheet: "Lineas Produccion",
      header_row: 1,
      rows_read: scenario === "many" ? 45_000 : 9,
      rows_with_errors: scenario === "many" ? 45_000 : errorRows.size,
      rows_with_warnings: 0,
      blocking: counts.some((count) => count.severity === "error"),
      counts,
    },
  };
}

let current: Draft = correcting ? validated() : uploaded;
let correction: Draft | null = null;

function comparisonOf(): Comparison {
  const previous = {
    draft_id: DRAFT_ID,
    filename: uploaded.filename,
    validated_at: "2026-10-17T10:00:06Z",
  };
  const none = { cleared: 0, remaining: 0, new: 0, by_count_only: [] };
  if (scenario === "rulesChanged") {
    return { state: "rules_changed", previous, previous_revalidated: false, ...none };
  }
  if (scenario === "previousGone") {
    return { state: "previous_gone", previous: null, previous_revalidated: false, ...none };
  }
  const remaining = correctionIssues.filter((issue) => issue.change === "remaining").length;
  return {
    state: "ready",
    previous,
    previous_revalidated: false,
    cleared: clearedIssues.length,
    remaining,
    new: correctionIssues.length - remaining,
    by_count_only: [],
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function page(url: URL, list: Issue[] = issues): IssuesPage {
  const severity = url.searchParams.get("severity");
  const rule = url.searchParams.get("rule");
  const column = url.searchParams.get("column");
  const change = url.searchParams.get("change");
  const offset = Number(url.searchParams.get("offset") ?? 0);
  const limit = Number(url.searchParams.get("limit") ?? 100);
  const matching = list.filter(
    (issue) =>
      (!severity || issue.severity === severity) &&
      (!rule || issue.rule === rule) &&
      (!column || issue.column_key === column) &&
      (!change || issue.change === change),
  );
  return { total: matching.length, offset, limit, items: matching.slice(offset, offset + limit) };
}

/** The API, answered in the browser. */
export async function previewFetch(input: RequestInfo | URL, init?: RequestInit) {
  const url = new URL(String(input), window.location.origin);
  const path = url.pathname;
  await wait(250);
  if (path === "/api/config") {
    return json({
      auth: {
        tenant_id: "11111111-1111-4111-8111-111111111111",
        client_id: "33333333-3333-4333-8333-333333333333",
        authority: "https://login.example.test/preview",
        api_scope: "api://preview/access_as_user",
      },
      powerbi: null,
    });
  }
  if (path === "/api/me") return json(me);
  if (path === "/api/health") {
    return json({
      status: "ok",
      service: "clarios-energy",
      version: "preview",
      environment: "dev",
    });
  }
  if (path === "/api/datasets") return json(datasets);
  if (path.endsWith("/template")) {
    return new Response(new Blob(["PK"]), {
      headers: { "Content-Disposition": 'attachment; filename="lineas_produccion.xlsx"' },
    });
  }
  if (path === `/api/drafts/${DRAFT_ID}`) return json(current);
  if (path === `/api/drafts/${DRAFT_ID}/validate` && init?.method === "POST") {
    await wait(1200);
    if (scenario === "large") {
      return json({ code: "not_implemented", message: "Increment 12", details: {} }, 501);
    }
    if (scenario === "busy") return json({ code: "draft_busy" }, 409);
    current = validated();
    return json(current);
  }
  if (path === `/api/drafts/${DRAFT_ID}/issues`) return json(page(url));
  if (path === `/api/drafts/${CORRECTION_ID}` && correction) return json(correction);
  if (path === `/api/drafts/${CORRECTION_ID}/validate` && init?.method === "POST" && correction) {
    await wait(1200);
    correction = {
      ...validated(correctionIssues, correction),
      previous_draft_id: DRAFT_ID,
      comparison: comparisonOf(),
    };
    return json(correction);
  }
  if (path === `/api/drafts/${CORRECTION_ID}/issues`) return json(page(url, correctionIssues));
  if (path === `/api/drafts/${CORRECTION_ID}/cleared`) {
    return json(page(url, clearedIssues));
  }
  return json({ code: "not_found" }, 404);
}

/** An upload that takes a few seconds, reports its progress and can be stopped. */
export class PreviewRequest {
  status = 0;
  responseText = "";
  upload: { onprogress: ((event: ProgressEvent) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  private timer: ReturnType<typeof setInterval> | undefined;
  private url = "";

  open(_method: string, url: string) {
    this.url = url;
  }
  setRequestHeader() {}

  send(body: File) {
    const total = body.size;
    const started = performance.now();
    this.timer = setInterval(() => {
      const elapsed = performance.now() - started;
      const sent = Math.min(total, Math.round((total * elapsed) / UPLOAD_MS));
      this.upload.onprogress?.({ loaded: sent, total, lengthComputable: true } as ProgressEvent);
      if (sent >= total) {
        clearInterval(this.timer);
        setTimeout(() => {
          if (scenario === "refused") {
            this.status = 413;
            this.responseText = JSON.stringify({ code: "file_too_large" });
          } else if (this.url.includes("/corrections") && scenario === "same") {
            this.status = 200;
            this.responseText = JSON.stringify(current);
          } else if (this.url.includes("/corrections")) {
            correction = {
              ...uploaded,
              draft_id: CORRECTION_ID,
              filename: body.name,
              size_bytes: total,
              declared_bytes: total,
              previous_draft_id: DRAFT_ID,
              comparison: { ...comparisonOf(), state: "not_validated" },
            };
            this.status = 201;
            this.responseText = JSON.stringify(correction);
          } else {
            current = {
              ...uploaded,
              filename: body.name,
              size_bytes: total,
              declared_bytes: total,
            };
            this.status = 201;
            this.responseText = JSON.stringify(current);
          }
          this.onload?.();
        }, 900);
      }
    }, 100);
  }

  abort() {
    clearInterval(this.timer);
    this.onabort?.();
  }
}

/** Someone signed in, whose token is never asked of Microsoft. */
export const previewAuth: AuthClient = {
  account: { name: me.name ?? "", username: me.username ?? "" },
  signInError: null,
  signIn: () => new Promise<never>(() => {}),
  signOut: () => new Promise<never>(() => {}),
  getApiToken: async () => "preview-token",
  getServiceToken: async () => ({ token: "preview", expiresAt: new Date(Date.now() + 3_600_000) }),
  authorizeService: () => new Promise<never>(() => {}),
};
