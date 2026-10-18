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
// The data are made up; no real workbook is behind them.
import type { Datasets } from "../api/datasets";
import type { Draft, Issue, IssuesPage, RuleCount } from "../api/drafts";
import type { Me } from "../api/me";
import type { AuthClient } from "../auth/authClient";

const MIB = 1024 * 1024;
const DRAFT_ID = "00000000-0000-4000-8000-000000000005";
const UPLOAD_MS = 4000;

const scenario = new URLSearchParams(window.location.search).get("scenario") ?? "broken";

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
      entry_modes: ["upload", "grid", "form"],
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
  },
  {
    row: 1,
    column: null,
    column_key: "Última Firma Automática",
    rule: "schema.column_missing",
    severity: "warning",
    value: null,
    params: { header: "Última Firma Automática" },
  },
  {
    row: 1,
    column: "J",
    column_key: "Comentarios",
    rule: "schema.column_unknown",
    severity: "info",
    value: "Comentarios",
    params: { header: "Comentarios" },
  },
  {
    row: 3,
    column: "A",
    column_key: "Línea",
    rule: "cell.required",
    severity: "error",
    value: null,
    params: {},
  },
  {
    row: 4,
    column: "A",
    column_key: "Línea",
    rule: "cell.pattern",
    severity: "error",
    value: "faform02",
    params: { pattern: "^[A-Z0-9]+$" },
  },
  {
    row: 6,
    column: "D",
    column_key: "Planificación de Unidades por Hora",
    rule: "cell.type",
    severity: "error",
    value: "doce",
    params: { type: "decimal" },
  },
  {
    row: 7,
    column: "D",
    column_key: "Planificación de Unidades por Hora",
    rule: "cell.range",
    severity: "error",
    value: "-5",
    params: { min: "0" },
  },
  {
    row: 9,
    column: "I",
    column_key: "Firma Automática",
    rule: "cell.allowed",
    severity: "error",
    value: "Tal vez",
    params: { allowed: ["Sí", "No"] },
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
  }));
}

const issues = scenario === "many" ? manyIssues() : scenario === "clean" ? [] : brokenIssues;

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
    };
    count.rows += scenario === "many" ? 45 : 1;
    count.listed += 1;
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
};

function validated(): Draft {
  const counts = countsOf(issues);
  const errorRows = new Set(
    issues.filter((issue) => issue.severity === "error" && issue.row !== 1).map((i) => i.row),
  );
  return {
    ...uploaded,
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

let current: Draft = uploaded;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function page(url: URL): IssuesPage {
  const severity = url.searchParams.get("severity");
  const rule = url.searchParams.get("rule");
  const column = url.searchParams.get("column");
  const offset = Number(url.searchParams.get("offset") ?? 0);
  const limit = Number(url.searchParams.get("limit") ?? 100);
  const matching = issues.filter(
    (issue) =>
      (!severity || issue.severity === severity) &&
      (!rule || issue.rule === rule) &&
      (!column || issue.column_key === column),
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

  open() {}
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
