// One uploaded file at its own address: what its validation found, the issues
// one by one with their filters and pages, and every state the page can be
// in (still validating, too large to validate yet, busy, gone).
import { act, configure, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Comparison, Draft, Issue, IssuesPage, RuleCount } from "../api/drafts";
import i18n from "../i18n";
import es from "../i18n/es.json";
import { draftPath } from "../paths";
import { calls, json, onWideScreen, renderAppAt, stubApi } from "../test/render";
import { type FakeRequest, fakeXhr, fileOf } from "../test/xhr";
import { asCell } from "./CopyIssues";

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
    previous_draft_id: null,
    comparison: null,
    ...changes,
  };
}

const counts: RuleCount[] = [
  {
    rule: "schema.header_mismatch",
    severity: "error",
    column: "Descripción",
    rows: 1,
    listed: 1,
    reasons: [{ reason: "header.near_match", rows: 1 }],
  },
  {
    rule: "cell.allowed",
    severity: "error",
    column: "Firma Automática",
    rows: 1,
    listed: 1,
    reasons: [{ reason: "default.cell.allowed", rows: 1 }],
  },
  {
    rule: "cell.pattern",
    severity: "error",
    column: "Almac",
    rows: 2500,
    listed: 1000,
    reasons: [{ reason: "code.lost_leading_zeros", rows: 2500 }],
  },
  {
    rule: "schema.column_missing",
    severity: "warning",
    column: "Programador",
    rows: 1,
    listed: 1,
    reasons: [{ reason: "header.similar", rows: 1 }],
  },
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
    reasons: [
      { reason: "header.near_match", params: { difference: "accent" } },
      { reason: "default.schema.header_mismatch", params: {} },
    ],
    change: null,
  },
  {
    row: 1,
    column: null,
    column_key: "Programador",
    rule: "schema.column_missing",
    severity: "warning",
    value: null,
    params: { header: "Programador" },
    reasons: [
      { reason: "header.similar", params: { found: "Programdor" } },
      { reason: "default.schema.column_missing", params: {} },
    ],
    change: null,
  },
  {
    row: 5,
    column: "B",
    column_key: "Almac",
    rule: "cell.pattern",
    severity: "error",
    value: "101",
    params: { pattern: "^[0-9]{4}$" },
    reasons: [
      { reason: "code.lost_leading_zeros", params: { digits: 4 } },
      { reason: "text.invisible_characters", params: { characters: ["nbsp"] } },
      { reason: "default.cell.pattern", params: {} },
    ],
    change: null,
  },
  {
    row: 9,
    column: "I",
    column_key: "Firma Automática",
    rule: "cell.allowed",
    severity: "error",
    value: "Tal vez",
    params: { allowed: ["Sí", "No"] },
    reasons: [{ reason: "default.cell.allowed", params: {} }],
    change: null,
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
    // Errors block the load: nothing offers to confirm it.
    expect(screen.queryByRole("button", { name: es.loads.confirm.confirm })).toBeNull();
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

// -----------------------------------------------------------------------------
// Increment 6: reasons, and the corrected file
// -----------------------------------------------------------------------------
const CORRECTION = "0b7a2d0e-6f0f-4c59-9a3e-2f1d4c5b6a71";
const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

function callsTo(fetchStub: Parameters<typeof calls>[0], address: string) {
  return calls(fetchStub).filter((call) => call.address === address);
}
const CORRECTION_ISSUES = `/api/drafts/${CORRECTION}/issues?offset=0&limit=100`;

function comparisonOf(changes: Partial<Comparison> = {}): Comparison {
  return {
    state: "ready",
    previous: { draft_id: ID, filename: "Líneas.xlsx", validated_at: "2026-10-17T10:00:02Z" },
    previous_revalidated: false,
    cleared: 34,
    remaining: 3,
    new: 1,
    by_count_only: [],
    ...changes,
  };
}

function correctionOf(comparison: Comparison, list: Issue[] = issues): Draft {
  const base = validated.validation as NonNullable<Draft["validation"]>;
  return draftOf({
    draft_id: CORRECTION,
    filename: "Líneas corregidas.xlsx",
    status: "validated",
    previous_draft_id: ID,
    comparison,
    validation:
      list.length === 0 ? { ...base, counts: [], rows_with_errors: 0, blocking: false } : base,
  });
}

describe("each issue", () => {
  it("says where, which rule, the value, why it happened and what to do in Excel", async () => {
    onWideScreen();
    stubApi({
      answers: { [`/api/drafts/${ID}`]: () => json(validated), [ISSUES]: () => json(page(issues)) },
    });
    renderAppAt(draftPath(ID));

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(5);

    const allowed = within(rows[4] as HTMLElement);
    expect(allowed.getByText("I9")).toBeInTheDocument();
    expect(allowed.getByText("Firma Automática")).toBeInTheDocument();
    expect(allowed.getByText(es.loads.rules.cell.allowed.name)).toBeInTheDocument();
    expect(allowed.getByText("«Tal vez»")).toBeInTheDocument();
    // A rule's own reason: its message says why, and its fix names the cell.
    expect(
      allowed.getByText(i18n.t("loads.rules.cell.allowed.message", { allowed: "Sí, No" })),
    ).toBeInTheDocument();
    expect(
      allowed.getByText(
        i18n.t("loads.reasons.default.cell.allowed.fix", { cell: "I9", allowed: "Sí, No" }),
      ),
    ).toBeInTheDocument();

    const almac = within(rows[3] as HTMLElement);
    expect(almac.getByText("B5")).toBeInTheDocument();
    expect(
      almac.getByText(i18n.t("loads.reasons.code.lost_leading_zeros.why", { digits: "4" })),
    ).toBeInTheDocument();
    expect(
      almac.getByText(i18n.t("loads.reasons.code.lost_leading_zeros.fix", { column: "B" })),
    ).toBeInTheDocument();
    expect(
      almac.getByText(i18n.t("loads.issues.expected", { pattern: "^[0-9]{4}$" })),
    ).toBeInTheDocument();

    const header = within(rows[1] as HTMLElement);
    expect(header.getByText("C1")).toBeInTheDocument();
    expect(
      header.getByText(
        i18n.t("loads.reasons.header.near_match.why", {
          found: "Descripcion",
          expected: "Descripción",
          difference: es.loads.differences.accent,
        }),
      ),
    ).toBeInTheDocument();

    // A missing column has a row and no letter: it is named as the row.
    const missing = within(rows[2] as HTMLElement);
    expect(missing.getByText(i18n.t("loads.issues.rowOnly", { row: 1 }))).toBeInTheDocument();
    expect(missing.getByText(es.loads.issues.empty)).toBeInTheDocument();
    expect(missing.getByText(es.loads.severity.warning)).toBeInTheDocument();
    expect(
      missing.getByText(i18n.t("loads.reasons.header.similar.why", { found: "Programdor" })),
    ).toBeInTheDocument();
  });

  it("keeps a second likely cause in a disclosure, closed", async () => {
    const user = userEvent.setup();
    onWideScreen();
    stubApi({
      answers: { [`/api/drafts/${ID}`]: () => json(validated), [ISSUES]: () => json(page(issues)) },
    });
    renderAppAt(draftPath(ID));

    const summary = await screen.findByText(i18n.t("loads.issues.otherCauses", { count: 1 }));
    const disclosure = summary.closest("details") as HTMLDetailsElement;
    expect(disclosure.open).toBe(false);
    await user.click(summary);

    expect(disclosure.open).toBe(true);
    expect(
      within(disclosure).getByText(
        i18n.t("loads.reasons.text.invisible_characters.why", {
          characters: es.loads.characters.nbsp,
        }),
      ),
    ).toBeInTheDocument();
  });

  it("is the same on a phone, as a card", async () => {
    stubApi({
      answers: { [`/api/drafts/${ID}`]: () => json(validated), [ISSUES]: () => json(page(issues)) },
    });
    renderAppAt(draftPath(ID));

    const almac = within((await cards())[2] as HTMLElement);
    expect(almac.getByText("B5")).toBeInTheDocument();
    expect(
      almac.getByText(i18n.t("loads.reasons.code.lost_leading_zeros.fix", { column: "B" })),
    ).toBeInTheDocument();
  });
});

describe("a corrected file", () => {
  it("goes up from the draft's page, and the new draft says what it cleared", async () => {
    const user = userEvent.setup();
    const opened = fakeXhr();
    const uploaded = draftOf({
      draft_id: CORRECTION,
      filename: "Líneas corregidas.xlsx",
      previous_draft_id: ID,
      comparison: comparisonOf({ state: "not_validated" }),
    });
    const fetchStub = stubApi({
      answers: {
        [`/api/drafts/${ID}`]: () => json(validated),
        [ISSUES]: () => json(page(issues)),
        [`/api/drafts/${CORRECTION}`]: () => json(uploaded),
        [`/api/drafts/${CORRECTION}/validate`]: () => json(correctionOf(comparisonOf())),
        [CORRECTION_ISSUES]: () => json(page(issues.slice(0, 1))),
      },
    });
    renderAppAt(draftPath(ID));

    await user.click(await screen.findByRole("button", { name: es.loads.correction.upload }));
    fireEvent.change(screen.getByTestId("correction-input"), {
      target: { files: [fileOf("Líneas corregidas.xlsx", 4096, XLSX)] },
    });
    await waitFor(() => expect(opened).toHaveLength(1));
    const request = opened[0] as FakeRequest;
    expect(request.url).toBe(`/api/drafts/${ID}/corrections`);
    act(() => request.progress(4096, 4096));
    act(() => request.respond(201, uploaded));

    expect(
      await screen.findByRole("heading", {
        name: [
          i18n.t("loads.comparison.cleared", { count: 34, n: "34" }),
          i18n.t("loads.comparison.remaining", { count: 3, n: "3" }),
          i18n.t("loads.comparison.new", { count: 1, n: "1" }),
        ].join(" · "),
      }),
    ).toBeInTheDocument();
    expect(callsTo(fetchStub, `/api/drafts/${CORRECTION}/validate`)).toHaveLength(1);
    expect(screen.getByText(es.loads.comparison.clearedLabel)).toBeInTheDocument();
  });

  it("can be cancelled while it goes up, and the page stays", async () => {
    const user = userEvent.setup();
    const opened = fakeXhr();
    stubApi({
      answers: { [`/api/drafts/${ID}`]: () => json(validated), [ISSUES]: () => json(page(issues)) },
    });
    renderAppAt(draftPath(ID));

    await user.click(await screen.findByRole("button", { name: es.loads.correction.upload }));
    fireEvent.change(screen.getByTestId("correction-input"), {
      target: { files: [fileOf("Líneas corregidas.xlsx", 4 * MIB, XLSX)] },
    });
    await waitFor(() => expect(opened).toHaveLength(1));
    act(() => (opened[0] as FakeRequest).progress(MIB, 4 * MIB));
    await user.click(screen.getByRole("button", { name: es.loads.upload.cancel }));

    expect((opened[0] as FakeRequest).aborted).toBe(true);
    expect(await screen.findByText(es.loads.upload.cancelled)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: es.loads.correction.upload })).toBeInTheDocument();
  });

  it("that is the same file again says so and stays", async () => {
    const user = userEvent.setup();
    const opened = fakeXhr();
    stubApi({
      answers: { [`/api/drafts/${ID}`]: () => json(validated), [ISSUES]: () => json(page(issues)) },
    });
    renderAppAt(draftPath(ID));

    await user.click(await screen.findByRole("button", { name: es.loads.correction.upload }));
    fireEvent.change(screen.getByTestId("correction-input"), {
      target: { files: [fileOf("Líneas.xlsx", 4096, XLSX)] },
    });
    await waitFor(() => expect(opened).toHaveLength(1));
    act(() => (opened[0] as FakeRequest).respond(200, validated));

    expect(await screen.findByText(es.loads.correction.same)).toBeInTheDocument();
  });

  it("dropped anywhere on the page goes up as a correction", async () => {
    const opened = fakeXhr();
    stubApi({
      answers: { [`/api/drafts/${ID}`]: () => json(validated), [ISSUES]: () => json(page(issues)) },
    });
    renderAppAt(draftPath(ID));

    const heading = await screen.findByText(es.loads.issues.heading);
    const file = fileOf("Líneas corregidas.xlsx", 4096, XLSX);
    fireEvent.dragEnter(heading, { dataTransfer: { files: [file] } });
    expect(screen.getByText(es.loads.correction.dropping)).toBeInTheDocument();
    fireEvent.drop(heading, { dataTransfer: { files: [file] } });

    await waitFor(() => expect(opened).toHaveLength(1));
    expect((opened[0] as FakeRequest).url).toBe(`/api/drafts/${ID}/corrections`);
  });

  it("that is no workbook is refused before anything is sent", async () => {
    const user = userEvent.setup();
    const opened = fakeXhr();
    stubApi({
      answers: { [`/api/drafts/${ID}`]: () => json(validated), [ISSUES]: () => json(page(issues)) },
    });
    renderAppAt(draftPath(ID));

    await user.click(await screen.findByRole("button", { name: es.loads.correction.upload }));
    fireEvent.change(screen.getByTestId("correction-input"), {
      target: { files: [fileOf("datos.csv", 4096)] },
    });

    expect(await screen.findByText(es.loads.file.notXlsx)).toBeInTheDocument();
    expect(opened).toHaveLength(0);
  });

  it("marks what is new, and filters the new or the remaining through the server", async () => {
    const user = userEvent.setup();
    onWideScreen();
    const newOnly = `/api/drafts/${CORRECTION}/issues?change=new&offset=0&limit=100`;
    const marked: Issue[] = [
      { ...(issues[3] as Issue), change: "remaining" },
      { ...(issues[2] as Issue), change: "new" },
    ];
    const fetchStub = stubApi({
      answers: {
        [`/api/drafts/${CORRECTION}`]: () => json(correctionOf(comparisonOf())),
        [CORRECTION_ISSUES]: () => json(page(marked)),
        [newOnly]: () => json(page(marked.slice(1))),
      },
    });
    renderAppAt(draftPath(CORRECTION));

    const table = await screen.findByRole("table");
    expect(within(table).getAllByText(es.loads.issues.new)).toHaveLength(1);
    // From the keyboard, as a person may: the first choice is "Nuevos".
    screen.getByPlaceholderText(es.loads.issues.anyChange).focus();
    await user.keyboard("{ArrowDown}{Enter}");

    await waitFor(() => expect(callsTo(fetchStub, newOnly)).toHaveLength(1));
  });

  it("lists what it cleared on request, and only then asks for it", async () => {
    const user = userEvent.setup();
    const cleared = `/api/drafts/${CORRECTION}/cleared?offset=0&limit=100`;
    const fetchStub = stubApi({
      answers: {
        [`/api/drafts/${CORRECTION}`]: () => json(correctionOf(comparisonOf({ cleared: 1 }))),
        [CORRECTION_ISSUES]: () => json(page(issues.slice(0, 1))),
        [cleared]: () => json(page(issues.slice(3, 4))),
      },
    });
    renderAppAt(draftPath(CORRECTION));

    const show = await screen.findByRole("button", {
      name: i18n.t("loads.comparison.showCleared", { count: 1, n: "1" }),
    });
    expect(callsTo(fetchStub, cleared)).toHaveLength(0);
    await user.click(show);

    const list = await screen.findByRole("list", { name: es.loads.comparison.clearedHeading });
    expect(within(list).getByText("I9", { exact: false })).toBeInTheDocument();
    expect(within(list).getByText("«Tal vez»")).toBeInTheDocument();
  });

  it("that fixed everything says so first", async () => {
    stubApi({
      answers: {
        [`/api/drafts/${CORRECTION}`]: () =>
          json(correctionOf(comparisonOf({ cleared: 4, remaining: 0, new: 0 }), [])),
        [CORRECTION_ISSUES]: () => json(page([])),
      },
    });
    renderAppAt(draftPath(CORRECTION));

    expect(
      await screen.findByRole("heading", {
        name: i18n.t("loads.comparison.allFixed", { count: 4, n: "4" }),
      }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: es.loads.correction.upload })).toBeNull();
  });

  it.each([
    ["previous_gone", es.loads.comparison.previousGone],
    ["previous_not_validated", es.loads.comparison.previousNotValidated],
    ["rules_changed", es.loads.comparison.rulesChanged],
  ] as const)("without a comparison (%s) says why in one line", async (state, text) => {
    stubApi({
      answers: {
        [`/api/drafts/${CORRECTION}`]: () =>
          json(
            correctionOf(comparisonOf({ state, previous: null, cleared: 0, remaining: 0, new: 0 })),
          ),
        [CORRECTION_ISSUES]: () => json(page(issues)),
      },
    });
    renderAppAt(draftPath(CORRECTION));

    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.queryByText(es.loads.issues.new)).toBeNull();
  });
});

describe("copying for Excel", () => {
  it("copies the issues of the filter as a table, values never read as formulas", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn(async (_text: string) => {});
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const all = `/api/drafts/${ID}/issues?offset=0&limit=500`;
    const formula: Issue = { ...(issues[3] as Issue), value: "=HYPERLINK(1)" };
    stubApi({
      answers: {
        [`/api/drafts/${ID}`]: () => json(validated),
        [ISSUES]: () => json(page(issues)),
        [all]: () => json({ ...page([...issues, formula]), limit: 500 }),
      },
    });
    renderAppAt(draftPath(ID));

    await user.click(await screen.findByRole("button", { name: es.loads.copy.button }));

    expect(
      await screen.findByText(i18n.t("loads.copy.done", { count: 5, n: "5" })),
    ).toBeInTheDocument();
    const text = writeText.mock.calls[0]?.[0] as string;
    const lines = text.split("\r\n");
    expect(lines).toHaveLength(6);
    expect(lines[0]?.split("\t")[1]).toBe(es.loads.copy.columns.cell);
    expect(lines[4]?.split("\t").slice(1, 4)).toEqual(["I9", "Firma Automática", "Tal vez"]);
    expect(lines[5]?.split("\t")[3]).toBe("'=HYPERLINK(1)");
  });
});

describe("asCell", () => {
  it.each([
    ["=1+1", "'=1+1"],
    ["@SUM(A1)", "'@SUM(A1)"],
    ["+cmd", "'+cmd"],
    ["-5", "-5"],
    ["-2,5", "-2,5"],
    ["una\tcelda\ncon saltos", "una celda con saltos"],
  ])("keeps %j as text", (value, cell) => {
    expect(asCell(value)).toBe(cell);
  });
});
