// One uploaded file at its own address: what its validation found, the issues
// one by one with their filters and pages, and every state the page can be
// in (still validating, too large to validate yet, busy, gone).
import { configure, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Draft, Issue, IssuesPage, RuleCount } from "../api/drafts";
import i18n from "../i18n";
import es from "../i18n/es.json";
import { draftPath } from "../paths";
import { calls, json, onWideScreen, renderAppAt, stubApi } from "../test/render";

const MIB = 1024 * 1024;
const ID = "0b7a2d0e-6f0f-4c59-9a3e-2f1d4c5b6a70";
const ISSUES = `/api/drafts/${ID}/issues?offset=0&limit=100`;

afterEach(() => {
  vi.unstubAllGlobals();
});

// These pages make several requests before they settle, and the whole suite
// runs at once: a second is too short a wait when the machine is busy.
configure({ asyncUtilTimeout: 5000 });

function draftOf(changes: Partial<Draft> = {}): Draft {
  return {
    draft_id: ID,
    dataset_key: "lineas_produccion",
    status: "uploaded",
    filename: "Líneas.xlsx",
    declared_bytes: 2 * MIB,
    size_bytes: 2 * MIB,
    content_hash: "a".repeat(64),
    created_at: "2026-10-17T10:00:00Z",
    uploaded_at: "2026-10-17T10:00:01Z",
    expires_at: "2026-11-16T10:00:01Z",
    failure_code: null,
    validation: null,
    ...changes,
  };
}

const counts: RuleCount[] = [
  { rule: "schema.header_mismatch", severity: "error", column: "Descripción", rows: 1, listed: 1 },
  { rule: "cell.allowed", severity: "error", column: "Firma Automática", rows: 1, listed: 1 },
  { rule: "cell.pattern", severity: "error", column: "Almac", rows: 2500, listed: 1000 },
  { rule: "schema.column_missing", severity: "warning", column: "Programador", rows: 1, listed: 1 },
];

const issues: Issue[] = [
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
    column_key: "Programador",
    rule: "schema.column_missing",
    severity: "warning",
    value: null,
    params: { header: "Programador" },
  },
  {
    row: 5,
    column: "B",
    column_key: "Almac",
    rule: "cell.pattern",
    severity: "error",
    value: "101",
    params: { pattern: "^[0-9]{4}$" },
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

const validated = draftOf({
  status: "validated",
  validation: {
    validated_at: "2026-10-17T10:00:02Z",
    sheet: "Lineas Produccion",
    header_row: 1,
    rows_read: 2600,
    rows_with_errors: 2501,
    rows_with_warnings: 0,
    blocking: true,
    counts,
  },
});

/** The issues on the page, on a phone: one card each. */
async function cards() {
  const list = await screen.findByRole("list", { name: es.loads.issues.heading });
  return within(list).getAllByRole("listitem");
}

function page(items: Issue[], total = items.length, offset = 0): IssuesPage {
  return { total, offset, limit: 100, items };
}

describe("a validated file", () => {
  it("says first whether it can be loaded, then the figures", async () => {
    stubApi({
      answers: { [`/api/drafts/${ID}`]: () => json(validated), [ISSUES]: () => json(page(issues)) },
    });
    renderAppAt(draftPath(ID));

    expect(
      await screen.findByRole("heading", {
        name: i18n.t("loads.result.blocking", { count: 2501, rows: "2501" }),
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("2600")).toBeInTheDocument();
    expect(screen.getByText("2501")).toBeInTheDocument();
    expect(screen.getByText(es.loads.result.nextStep)).toBeInTheDocument();
    expect(
      screen.getByText(
        i18n.t("loads.issues.capped", {
          column: "Almac",
          rule: es.loads.rules.cell.pattern.name,
          rows: "2500",
          listed: "1000",
        }),
      ),
    ).toBeInTheDocument();
  });

  it("lists every issue with its row, column, rule, value and sentence", async () => {
    onWideScreen();
    stubApi({
      answers: { [`/api/drafts/${ID}`]: () => json(validated), [ISSUES]: () => json(page(issues)) },
    });
    renderAppAt(draftPath(ID));

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(5);
    const allowed = within(rows[4] as HTMLElement);
    expect(allowed.getByText("9")).toBeInTheDocument();
    expect(allowed.getByText("Firma Automática", { exact: false })).toBeInTheDocument();
    expect(allowed.getByText(es.loads.rules.cell.allowed.name)).toBeInTheDocument();
    expect(allowed.getByText("«Tal vez»")).toBeInTheDocument();
    expect(
      allowed.getByText(i18n.t("loads.rules.cell.allowed.message", { allowed: "Sí, No" })),
    ).toBeInTheDocument();
    const pattern = within(rows[3] as HTMLElement);
    expect(
      pattern.getByText(i18n.t("loads.issues.expected", { pattern: "^[0-9]{4}$" })),
    ).toBeInTheDocument();
    const missing = within(rows[2] as HTMLElement);
    expect(missing.getByText(es.loads.issues.empty)).toBeInTheDocument();
    expect(missing.getByText(es.loads.severity.warning)).toBeInTheDocument();
  });

  it("is a list of cards on a phone", async () => {
    stubApi({
      answers: { [`/api/drafts/${ID}`]: () => json(validated), [ISSUES]: () => json(page(issues)) },
    });
    renderAppAt(draftPath(ID));

    expect(await cards()).toHaveLength(4);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("filters by severity, rule and column through the server", async () => {
    const user = userEvent.setup();
    const errorsOnly = `/api/drafts/${ID}/issues?severity=error&offset=0&limit=100`;
    const fetchStub = stubApi({
      answers: {
        [`/api/drafts/${ID}`]: () => json(validated),
        [ISSUES]: () => json(page(issues)),
        [errorsOnly]: () => json(page(issues.filter((issue) => issue.severity === "error"))),
      },
    });
    renderAppAt(draftPath(ID));
    await cards();

    await user.click(screen.getByText(i18n.t("loads.issues.errorCount", { n: "1002" })));

    await waitFor(async () => expect(await cards()).toHaveLength(3));
    expect(calls(fetchStub).map((call) => call.address)).toContain(errorsOnly);
  });

  it("offers to clear filters that match nothing", async () => {
    const user = userEvent.setup();
    const warnings = `/api/drafts/${ID}/issues?severity=warning&offset=0&limit=100`;
    stubApi({
      answers: {
        [`/api/drafts/${ID}`]: () => json(validated),
        [ISSUES]: () => json(page(issues)),
        [warnings]: () => json(page([])),
      },
    });
    renderAppAt(draftPath(ID));
    await cards();

    await user.click(screen.getByText(i18n.t("loads.issues.warningCount", { n: "1" })));
    await user.click(await screen.findByRole("button", { name: es.loads.issues.clearFilters }));

    expect(await cards()).toHaveLength(4);
  });

  it("pages through more issues than a page holds", async () => {
    const user = userEvent.setup();
    const second = `/api/drafts/${ID}/issues?offset=100&limit=100`;
    const fetchStub = stubApi({
      answers: {
        [`/api/drafts/${ID}`]: () => json(validated),
        [ISSUES]: () => json(page(issues, 104)),
        [second]: () => json(page(issues.slice(0, 1), 104, 100)),
      },
    });
    renderAppAt(draftPath(ID));

    expect(
      await screen.findByText(i18n.t("loads.issues.range", { from: "1", to: "4", total: "104" })),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: es.loads.issues.previous })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: es.loads.issues.next }));

    expect(
      await screen.findByText(
        i18n.t("loads.issues.range", { from: "101", to: "101", total: "104" }),
      ),
    ).toBeInTheDocument();
    expect(calls(fetchStub).map((call) => call.address)).toContain(second);
  });

  it("says that no rule found anything", async () => {
    const clean = draftOf({
      status: "validated",
      validation: {
        ...(validated.validation as NonNullable<Draft["validation"]>),
        rows_with_errors: 0,
        blocking: false,
        counts: [],
      },
    });
    stubApi({ answers: { [`/api/drafts/${ID}`]: () => json(clean) } });
    renderAppAt(draftPath(ID));

    expect(await screen.findByText(es.loads.result.clean)).toBeInTheDocument();
    expect(screen.getByText(es.loads.issues.none)).toBeInTheDocument();
  });

  it("offers a retry when the issues cannot be read", async () => {
    let failing = true;
    stubApi({
      answers: {
        [`/api/drafts/${ID}`]: () => json(validated),
        [ISSUES]: () => (failing ? json({ code: "boom" }, 500) : json(page(issues))),
      },
    });
    renderAppAt(draftPath(ID));

    const user = userEvent.setup();
    expect(
      await screen.findByText(es.loads.issues.failed, {}, { timeout: 8000 }),
    ).toBeInTheDocument();
    failing = false;
    await user.click(screen.getByRole("button", { name: es.problem.retry }));

    expect(await cards()).toHaveLength(4);
  }, 15_000);
});

describe("a file not validated yet", () => {
  it("is validated on arrival from an upload, once", async () => {
    const fetchStub = stubApi({
      answers: {
        [`/api/drafts/${ID}`]: () => json(draftOf()),
        [`/api/drafts/${ID}/validate`]: () => json(validated),
        [ISSUES]: () => json(page(issues)),
      },
    });
    renderAppAt({ pathname: draftPath(ID), state: { validate: true } });

    expect(
      await screen.findByRole("heading", {
        name: i18n.t("loads.result.blocking", { count: 2501, rows: "2501" }),
      }),
    ).toBeInTheDocument();
    expect(calls(fetchStub).filter((call) => call.address.endsWith("/validate"))).toHaveLength(1);
  });

  it("offers to validate when opened later", async () => {
    const user = userEvent.setup();
    stubApi({
      answers: {
        [`/api/drafts/${ID}`]: () => json(draftOf()),
        [`/api/drafts/${ID}/validate`]: () => json(validated),
        [ISSUES]: () => json(page(issues)),
      },
    });
    renderAppAt(draftPath(ID));

    await user.click(await screen.findByRole("button", { name: es.loads.draft.validate }));

    expect(
      await screen.findByRole("heading", {
        name: i18n.t("loads.result.blocking", { count: 2501, rows: "2501" }),
      }),
    ).toBeInTheDocument();
  });

  it("says that a file over 5 MB was kept, when the server answers 501", async () => {
    stubApi({
      answers: {
        [`/api/drafts/${ID}`]: () => json(draftOf()),
        [`/api/drafts/${ID}/validate`]: () =>
          json({ code: "not_implemented", message: "Increment 12", details: {} }, 501),
      },
    });
    renderAppAt({ pathname: draftPath(ID), state: { validate: true } });

    expect(await screen.findByText(es.loads.draft.archivedTitle)).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t("loads.draft.notValidated", { limit: "5 MB" })),
    ).toBeInTheDocument();
  });

  it("knows a large file is only kept, without asking to validate it", async () => {
    const fetchStub = stubApi({
      answers: { [`/api/drafts/${ID}`]: () => json(draftOf({ size_bytes: 300 * MIB })) },
    });
    renderAppAt(draftPath(ID));

    expect(await screen.findByText(es.loads.draft.archivedTitle)).toBeInTheDocument();
    expect(calls(fetchStub).filter((call) => call.address.endsWith("/validate"))).toHaveLength(0);
  });

  it("says so when another validation of it is running", async () => {
    const user = userEvent.setup();
    stubApi({
      answers: {
        [`/api/drafts/${ID}`]: () => json(draftOf()),
        [`/api/drafts/${ID}/validate`]: () => json({ code: "draft_busy" }, 409),
      },
    });
    renderAppAt(draftPath(ID));

    await user.click(await screen.findByRole("button", { name: es.loads.draft.validate }));

    expect(await screen.findByRole("alert")).toHaveTextContent(es.loads.draft.busy);
  });
});

describe("a draft that cannot be shown", () => {
  it("is gone: someone else's, expired, or never there", async () => {
    stubApi();
    renderAppAt(draftPath(ID));

    expect(await screen.findByText(es.loads.draft.missing)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: es.loads.draft.another })).toBeInTheDocument();
  });

  it("never finished arriving", async () => {
    stubApi({
      answers: {
        [`/api/drafts/${ID}`]: () =>
          json(draftOf({ status: "failed", failure_code: "client_disconnected" })),
      },
    });
    renderAppAt(draftPath(ID));

    expect(await screen.findByText(es.loads.draft.unfinished)).toBeInTheDocument();
  });
});
