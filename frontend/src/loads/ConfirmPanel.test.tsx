// Confirming a draft into the database, on its page: what the confirmation
// says, the acceptance of warnings, one request however many presses, the
// result in place, a catalogue's changes, and every refusal said plainly.
import { configure, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Confirmation, DifferencePage, Draft } from "../api/drafts";
import type { LoadDetail } from "../api/loads";
import i18n from "../i18n";
import es from "../i18n/es.json";
import { draftPath, loadPath } from "../paths";
import { calls, json, onWideScreen, renderAppAt, stubApi } from "../test/render";

const ID = "0b7a2d0e-6f0f-4c59-9a3e-2f1d4c5b6a71";
const RUN = "0b7a2d0e-6f0f-4c59-9a3e-2f1d4c5b6a72";
const BASE = "0b7a2d0e-6f0f-4c59-9a3e-2f1d4c5b6a73";
const DIGEST = "d".repeat(64);
const DRAFT = `/api/drafts/${ID}`;
const CONFIRMATION = `/api/drafts/${ID}/confirmation`;
const COMMIT = `/api/drafts/${ID}/commit`;
const ISSUES = `/api/drafts/${ID}/issues?offset=0&limit=100`;

afterEach(() => {
  vi.unstubAllGlobals();
});

configure({ asyncUtilTimeout: 5000 });

function draftOf(changes: Partial<Draft> = {}): Draft {
  return {
    draft_id: ID,
    dataset_key: "qad_facturas",
    status: "validated",
    filename: "Facturas octubre.xlsx",
    declared_bytes: 7000,
    size_bytes: 7000,
    content_hash: "a".repeat(64),
    created_at: "2026-10-18T10:00:00Z",
    uploaded_at: "2026-10-18T10:00:01Z",
    expires_at: "2026-11-17T10:00:01Z",
    failure_code: null,
    validation: {
      validated_at: "2026-10-18T10:00:02Z",
      sheet: "Datos",
      header_row: 1,
      rows_read: 4,
      rows_with_errors: 0,
      rows_with_warnings: 0,
      blocking: false,
      counts: [],
    },
    previous_draft_id: null,
    comparison: null,
    ...changes,
  };
}

function confirmationOf(changes: Partial<Confirmation> = {}): Confirmation {
  return {
    draft_id: ID,
    dataset_key: "qad_facturas",
    kind: "movement",
    filename: "Facturas octubre.xlsx",
    sheet: "Datos",
    can_confirm: true,
    blocked_by: null,
    validation_digest: DIGEST,
    rows: 4,
    warnings: [],
    warnings_to_accept: false,
    period: { start: "2025-10-03", end: "2025-10-31" },
    overlapping_loads: [],
    current_version: null,
    difference: null,
    already_loaded_run_id: null,
    run_id: null,
    ...changes,
  };
}

const committed = draftOf({
  status: "committed",
  run_id: RUN,
  committed_at: "2026-10-18T10:05:00Z",
});

function loadOf(): LoadDetail {
  return {
    run_id: RUN,
    origin: "app",
    state: "completed",
    source_key: "qad_facturas",
    dataset_key: "qad_facturas",
    sheets: [{ name: "Datos", dataset_key: null, rows: 4 }],
    filename: "Facturas octubre.xlsx",
    rows: 4,
    started_at: "2026-10-18T10:05:00Z",
    finished_at: "2026-10-18T10:05:00Z",
    window_start: "2025-10-03",
    window_end: "2025-10-31",
    uploaded_by_name: "Ada Lovelace",
    mine: true,
    warnings_accepted: 0,
    error: "",
    plant_code: "1515",
    flow_name: "Facturas octubre",
    original: { filename: "Facturas octubre.xlsx", size_bytes: 7000, content_hash: "a".repeat(64) },
    validation: null,
    difference: null,
    draft_id: ID,
    draft_available: true,
  };
}

const noIssues = () => json({ total: 0, offset: 0, limit: 100, items: [] });

/**
 * The API for one draft: the draft answers validated until the commit lands,
 * then committed, as the server does.
 */
function stubDraft(
  shown: Confirmation,
  commit: (init?: RequestInit) => Response | Promise<Response> = () => json(loadOf(), 201),
  draft: Draft = draftOf(),
) {
  let landed = false;
  return stubApi({
    answers: {
      [DRAFT]: () => json(landed ? committed : draft),
      [CONFIRMATION]: () => json(shown),
      [ISSUES]: noIssues,
      [COMMIT]: async (init) => {
        const answer = await commit(init);
        if (answer.ok) landed = true;
        return answer;
      },
    },
  });
}

function commitCalls(fetchStub: ReturnType<typeof stubApi>) {
  return calls(fetchStub).filter((call) => call.address === COMMIT);
}

function bodyOf(fetchStub: ReturnType<typeof stubApi>, index = 0): unknown {
  const call = fetchStub.mock.calls.filter(([input]) => String(input) === COMMIT)[index];
  const init = call?.[1] as RequestInit | undefined;
  return JSON.parse(String(init?.body));
}

describe("the confirmation of a movement", () => {
  it("says how many rows land where, and the period they cover", async () => {
    stubDraft(confirmationOf());
    renderAppAt(draftPath(ID));

    expect(
      await screen.findByRole("heading", {
        name: i18n.t("loads.confirm.ready", {
          count: 4,
          rows: "4",
          dataset: es.datasets.qad_facturas.name,
        }),
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(i18n.t("loads.confirm.period", { from: "3/10/25", to: "31/10/25" })),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: es.loads.confirm.confirm })).toBeEnabled();
  });

  it("names the loads that already cover part of the period", async () => {
    stubDraft(
      confirmationOf({
        overlapping_loads: [
          {
            run_id: BASE,
            origin: "cli",
            finished_at: "2026-10-01T08:00:00Z",
            filename: "Facturas viejas.xlsx",
            rows: 100,
            uploaded_by_name: null,
            window_start: "2025-10-20",
            window_end: "2025-11-05",
          },
        ],
      }),
    );
    renderAppAt(draftPath(ID));

    expect(
      await screen.findByText(i18n.t("loads.confirm.overlapping", { count: 1 })),
    ).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Facturas viejas\.xlsx/ });
    expect(link).toHaveAttribute("href", loadPath(BASE));
    expect(screen.getByText(es.loads.confirm.overlappingRule)).toBeInTheDocument();
  });
});

describe("confirming", () => {
  it("sends what the person saw, once, and shows the load in place", async () => {
    const user = userEvent.setup();
    const fetchStub = stubDraft(confirmationOf());
    renderAppAt(draftPath(ID));

    await user.click(await screen.findByRole("button", { name: es.loads.confirm.confirm }));

    expect(
      await screen.findByRole("heading", { name: es.loads.committed.title }),
    ).toBeInTheDocument();
    expect(commitCalls(fetchStub)).toHaveLength(1);
    expect(commitCalls(fetchStub)[0]?.method).toBe("POST");
    expect(bodyOf(fetchStub)).toEqual({
      validation_digest: DIGEST,
      accept_warnings: false,
      base_run_id: null,
    });
    expect(screen.getByRole("link", { name: es.loads.committed.detail })).toHaveAttribute(
      "href",
      loadPath(RUN),
    );
    expect(screen.queryByRole("button", { name: es.loads.confirm.confirm })).toBeNull();
    expect(screen.getByText(es.loads.committed.figures)).toBeInTheDocument();
  });

  it("sends one request however many times the button is pressed", async () => {
    const user = userEvent.setup();
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const fetchStub = stubDraft(confirmationOf(), async () => {
      await held;
      return json(loadOf(), 201);
    });
    renderAppAt(draftPath(ID));
    const button = await screen.findByRole("button", { name: es.loads.confirm.confirm });

    await user.dblClick(button);
    await user.click(button);

    expect(await screen.findByRole("button", { name: es.loads.confirm.confirming })).toBeDisabled();
    expect(screen.getByText(es.loads.confirm.noCancel)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: es.loads.upload.cancel })).toBeNull();
    release();
    expect(
      await screen.findByRole("heading", { name: es.loads.committed.title }),
    ).toBeInTheDocument();
    expect(commitCalls(fetchStub)).toHaveLength(1);
  });

  it("waits for warnings to be accepted, and says they were", async () => {
    const user = userEvent.setup();
    const fetchStub = stubDraft(
      confirmationOf({
        warnings_to_accept: true,
        warnings: [
          {
            rule: "cell.text_damaged",
            column: "Número Artículo",
            rows: 3,
            reasons: [{ reason: "code.scientific_notation", rows: 3 }],
          },
        ],
      }),
    );
    renderAppAt(draftPath(ID));

    const button = await screen.findByRole("button", { name: es.loads.confirm.confirm });
    expect(button).toBeDisabled();
    expect(screen.getByText(es.loads.confirm.acceptFirst)).toBeInTheDocument();
    expect(
      screen.getByText(
        i18n.t("loads.confirm.warningLine", {
          count: 3,
          rule: es.loads.rules.cell.text_damaged.name,
          column: "Número Artículo",
          rows: "3",
        }),
      ),
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("checkbox", { name: i18n.t("loads.confirm.accept", { count: 3, n: "3" }) }),
    );
    await user.click(button);

    await screen.findByRole("heading", { name: es.loads.committed.title });
    expect(bodyOf(fetchStub)).toMatchObject({ accept_warnings: true });
  });

  it("says when the validation changed, and asks the confirmation again", async () => {
    const user = userEvent.setup();
    const fetchStub = stubDraft(confirmationOf(), () =>
      json({ code: "validation_changed", message: "x", details: {} }, 409),
    );
    renderAppAt(draftPath(ID));

    await user.click(await screen.findByRole("button", { name: es.loads.confirm.confirm }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      es.loads.confirm.errors.validationChanged,
    );
    await waitFor(() =>
      expect(
        calls(fetchStub).filter((call) => call.address === CONFIRMATION).length,
      ).toBeGreaterThan(1),
    );
  });

  it("checks a commit whose answer was lost, and finds it landed", async () => {
    const user = userEvent.setup();
    let attempts = 0;
    const fetchStub = stubDraft(confirmationOf(), () => {
      attempts += 1;
      if (attempts === 1) throw new TypeError("Failed to fetch");
      return json(loadOf(), 200);
    });
    renderAppAt(draftPath(ID));

    await user.click(await screen.findByRole("button", { name: es.loads.confirm.confirm }));
    expect(await screen.findByRole("alert")).toHaveTextContent(es.loads.confirm.errors.unknown);
    await user.click(screen.getByRole("button", { name: es.loads.confirm.check }));

    expect(
      await screen.findByRole("heading", { name: es.loads.committed.title }),
    ).toBeInTheDocument();
    expect(commitCalls(fetchStub)).toHaveLength(2);
  });

  it("offers nothing to confirm for a file already loaded, but where it is", async () => {
    stubDraft(
      confirmationOf({
        can_confirm: false,
        blocked_by: "already_loaded",
        already_loaded_run_id: RUN,
      }),
    );
    renderAppAt(draftPath(ID));

    expect(await screen.findByText(es.loads.confirm.alreadyLoaded)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: es.loads.confirm.seeLoad })).toHaveAttribute(
      "href",
      loadPath(RUN),
    );
    expect(screen.queryByRole("button", { name: es.loads.confirm.confirm })).toBeNull();
  });

  it("is the one primary action: the corrected file's upload is quiet beside it", async () => {
    stubDraft(
      confirmationOf(),
      undefined,
      draftOf({
        validation: {
          validated_at: "2026-10-18T10:00:02Z",
          sheet: "Datos",
          header_row: 1,
          rows_read: 4,
          rows_with_errors: 0,
          rows_with_warnings: 1,
          blocking: false,
          counts: [
            {
              rule: "cell.text_damaged",
              severity: "warning",
              column: "X",
              rows: 1,
              listed: 1,
              reasons: [],
            },
          ],
        },
      }),
    );
    renderAppAt(draftPath(ID));

    const confirm = await screen.findByRole("button", { name: es.loads.confirm.confirm });
    const upload = screen.getByRole("button", { name: es.loads.correction.upload });
    expect(confirm).toHaveAttribute("data-variant", "filled");
    expect(upload).toHaveAttribute("data-variant", "default");
  });
});

describe("the confirmation of a catalogue", () => {
  const catalogue = draftOf({ dataset_key: "lineas_produccion", filename: "Líneas.xlsx" });

  function changesPage(change: "added" | "changed" | "removed"): DifferencePage {
    const items = {
      added: [
        {
          change,
          key: ["PLACA03"],
          row: 4,
          previous_row: null,
          values: { Línea: "PLACA03", Descripción: "Placas 3" },
          columns: [],
        },
      ],
      changed: [
        {
          change,
          key: ["FAFORM01"],
          row: 2,
          previous_row: 2,
          values: {},
          columns: [{ column: "Descripción", before: "Formación 1", after: "Formación uno" }],
        },
      ],
      removed: [
        {
          change,
          key: ["MONT04"],
          row: null,
          previous_row: 4,
          values: { Línea: "MONT04", Descripción: "Montaje 4" },
          columns: [],
        },
      ],
    }[change];
    return { total: items.length, offset: 0, limit: 50, items };
  }

  it("shows how it differs from the current version, and the rows on request", async () => {
    const user = userEvent.setup();
    onWideScreen();
    const answers = stubApi({
      answers: {
        [DRAFT]: () => json(catalogue),
        [ISSUES]: noIssues,
        [CONFIRMATION]: () =>
          json(
            confirmationOf({
              dataset_key: "lineas_produccion",
              kind: "snapshot",
              period: null,
              current_version: {
                run_id: BASE,
                origin: "cli",
                finished_at: "2026-10-07T08:00:00Z",
                filename: "EQUS BATT.xlsx",
                rows: 4,
                uploaded_by_name: null,
                window_start: null,
                window_end: null,
              },
              difference: {
                base_run_id: BASE,
                added: 1,
                changed: 1,
                removed: 1,
                unchanged: 2,
                removed_rows: "kept",
              },
            }),
          ),
        ...Object.fromEntries(
          (["added", "changed", "removed"] as const).map((change) => [
            `/api/drafts/${ID}/difference?change=${change}&offset=0&limit=50`,
            () => json(changesPage(change)),
          ]),
        ),
      },
    });
    renderAppAt(draftPath(ID));

    expect(await screen.findByText(es.loads.confirm.unchanged)).toBeInTheDocument();
    expect(screen.getByText(es.datasets.lineas_produccion.removedRows)).toBeInTheDocument();
    expect(screen.getByText(/EQUS BATT\.xlsx/)).toBeInTheDocument();
    const before = calls(answers).filter((call) => call.address.includes("/difference")).length;
    expect(before).toBe(0);

    await user.click(screen.getByText(es.loads.difference.show));
    expect(await screen.findByText("PLACA03")).toBeInTheDocument();
    await user.click(
      screen.getByRole("radio", { name: i18n.t("loads.difference.changed", { n: "1" }) }),
    );
    const row = (await screen.findByText("FAFORM01")).closest("tr") as HTMLElement;
    expect(within(row).getByText("«Formación 1»")).toBeInTheDocument();
    expect(within(row).getByText("«Formación uno»")).toBeInTheDocument();
  });
});
