// The history of loads and one load in detail: every load whichever way it
// came, the filters kept in the address and sent to the server, the empty
// and failing states, the pages, and the original's download through the API.
import { configure, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LoadDetail, LoadSummary, LoadsPage } from "../api/loads";
import i18n from "../i18n";
import es from "../i18n/es.json";
import { draftPath, loadPath, paths } from "../paths";
import { calls, json, onWideScreen, renderAppAt, stubApi } from "../test/render";

const APP_RUN = "0b7a2d0e-6f0f-4c59-9a3e-2f1d4c5b6a81";
const CLI_RUN = "0b7a2d0e-6f0f-4c59-9a3e-2f1d4c5b6a82";
const DRAFT = "0b7a2d0e-6f0f-4c59-9a3e-2f1d4c5b6a83";
const FIRST_PAGE = "/api/loads?offset=0&limit=25";

afterEach(() => {
  vi.unstubAllGlobals();
});

configure({ asyncUtilTimeout: 5000 });

function summaryOf(changes: Partial<LoadSummary> = {}): LoadSummary {
  return {
    run_id: APP_RUN,
    origin: "app",
    state: "completed",
    source_key: "manual_sheets",
    dataset_key: "lineas_produccion",
    sheets: [{ name: "Lineas Produccion", dataset_key: "lineas_produccion", rows: null }],
    filename: "Líneas.xlsx",
    rows: 64,
    started_at: "2026-10-18T10:05:00Z",
    finished_at: "2026-10-18T10:05:00Z",
    window_start: null,
    window_end: null,
    uploaded_by_name: "Ada Lovelace",
    mine: true,
    warnings_accepted: 1,
    error: "",
    ...changes,
  };
}

const fromCli = summaryOf({
  run_id: CLI_RUN,
  origin: "cli",
  source_key: "qad_facturas",
  dataset_key: "qad_facturas",
  sheets: [{ name: "Datos", dataset_key: null, rows: null }],
  filename: "Facturas.xlsx",
  rows: 14458,
  uploaded_by_name: null,
  mine: false,
  warnings_accepted: 0,
  window_start: "2025-10-01",
  window_end: "2025-10-31",
});

function pageOf(items: LoadSummary[], total = items.length): LoadsPage {
  return {
    total,
    offset: 0,
    limit: 25,
    items,
    people: [{ subject: "00000000-0000-4000-8000-00000000abcd", name: "Ada Lovelace" }],
  };
}

describe("the history", () => {
  it("lists every load, the app's and the command line's, in a table", async () => {
    onWideScreen();
    stubApi({ answers: { [FIRST_PAGE]: () => json(pageOf([summaryOf(), fromCli])) } });
    renderAppAt(paths.history);

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    const app = within(rows[1] as HTMLElement);
    expect(app.getByText(es.datasets.lineas_produccion.name)).toBeInTheDocument();
    expect(app.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(app.getByText(es.history.states.completed)).toBeInTheDocument();
    expect(app.getByRole("link")).toHaveAttribute("href", loadPath(APP_RUN));
    const cli = within(rows[2] as HTMLElement);
    expect(cli.getByText(es.history.commandLine)).toBeInTheDocument();
    expect(cli.getByText("14.458")).toBeInTheDocument();
    // One page: nothing to page through, so no pager.
    expect(screen.queryByRole("button", { name: es.history.next })).toBeNull();
    expect(screen.getByRole("link", { name: es.loads.tabs.history })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("is a list of cards on a phone", async () => {
    stubApi({ answers: { [FIRST_PAGE]: () => json(pageOf([summaryOf(), fromCli])) } });
    renderAppAt(paths.history);

    const list = await screen.findByRole("list", { name: es.loads.tabs.history });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("sends its filters to the server and keeps them in the address", async () => {
    const user = userEvent.setup();
    const fetchStub = stubApi({
      answers: {
        [FIRST_PAGE]: () => json(pageOf([summaryOf(), fromCli])),
        "/api/loads?user=me&offset=0&limit=25": () => json(pageOf([summaryOf()])),
      },
    });
    renderAppAt(paths.history);
    await screen.findByRole("list", { name: es.loads.tabs.history });

    // Mantine's select opens from the keyboard in jsdom; "Mis cargas" is first.
    screen.getByPlaceholderText(es.history.filters.anyPerson).focus();
    await user.keyboard("{ArrowDown}{Enter}");

    await waitFor(() =>
      expect(calls(fetchStub).map((call) => call.address)).toContain(
        "/api/loads?user=me&offset=0&limit=25",
      ),
    );
  });

  it("opens with the filters of its address", async () => {
    const fetchStub = stubApi({
      answers: {
        "/api/loads?dataset=qad_facturas&state=failed&offset=0&limit=25": () => json(pageOf([])),
      },
    });
    renderAppAt({ pathname: paths.history, search: "?dataset=qad_facturas&state=failed" });

    expect(await screen.findByText(es.history.noMatch)).toBeInTheDocument();
    expect(calls(fetchStub).map((call) => call.address)).toContain(
      "/api/loads?dataset=qad_facturas&state=failed&offset=0&limit=25",
    );
    expect(screen.getByRole("button", { name: es.history.clearFilters })).toBeInTheDocument();
  });

  it("says when there is nothing yet, and where to start", async () => {
    stubApi({ answers: { [FIRST_PAGE]: () => json(pageOf([])) } });
    renderAppAt(paths.history);

    expect(await screen.findByText(es.history.empty)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: es.history.emptyAction })).toHaveAttribute(
      "href",
      paths.loads,
    );
  });

  it("offers a retry when the history cannot be read", async () => {
    let failing = true;
    stubApi({
      answers: {
        [FIRST_PAGE]: () => (failing ? json({ code: "boom" }, 500) : json(pageOf([summaryOf()]))),
      },
    });
    renderAppAt(paths.history);

    const user = userEvent.setup();
    expect(await screen.findByText(es.history.failed, {}, { timeout: 5000 })).toBeInTheDocument();
    failing = false;
    await user.click(screen.getByRole("button", { name: es.problem.retry }));

    expect(await screen.findByRole("list", { name: es.loads.tabs.history })).toBeInTheDocument();
  });

  it("pages through more loads than a page holds", async () => {
    const user = userEvent.setup();
    const fetchStub = stubApi({
      answers: {
        [FIRST_PAGE]: () => json(pageOf([summaryOf()], 30)),
        "/api/loads?offset=25&limit=25": () => json({ ...pageOf([fromCli], 30), offset: 25 }),
      },
    });
    renderAppAt(paths.history);

    expect(
      await screen.findByText(i18n.t("history.range", { from: "1", to: "1", total: "30" })),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: es.history.next }));

    await waitFor(() =>
      expect(calls(fetchStub).map((call) => call.address)).toContain(
        "/api/loads?offset=25&limit=25",
      ),
    );
  });
});

describe("a load in detail", () => {
  function detailOf(changes: Partial<LoadDetail> = {}): LoadDetail {
    return {
      ...summaryOf({
        sheets: [{ name: "Lineas Produccion", dataset_key: "lineas_produccion", rows: 64 }],
      }),
      plant_code: "1515",
      flow_name: "Líneas",
      original: { filename: "Líneas.xlsx", size_bytes: 5240, content_hash: "f".repeat(64) },
      validation: {
        validated_at: "2026-10-18T10:00:00Z",
        rows_read: 64,
        rows_with_warnings: 1,
        counts: [
          { rule: "cell.text_damaged", severity: "warning", column: "Programador", rows: 1 },
        ],
        accepted: [
          { rule: "cell.text_damaged", severity: "warning", column: "Programador", rows: 1 },
        ],
      },
      difference: {
        base_run_id: CLI_RUN,
        added: 1,
        changed: 2,
        removed: 3,
        unchanged: 58,
        removed_rows: "kept",
      },
      draft_id: DRAFT,
      draft_available: true,
      ...changes,
    };
  }

  it("says what landed, from which file, and how it differed", async () => {
    stubApi({ answers: { [`/api/loads/${APP_RUN}`]: () => json(detailOf()) } });
    renderAppAt(loadPath(APP_RUN));

    expect(await screen.findByText(es.load.rowsWritten)).toBeInTheDocument();
    expect(
      screen.getByText(
        i18n.t("load.sheetRows", { count: 64, name: "Lineas Produccion", rows: "64" }),
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("ffffffffffff…")).toHaveAttribute("title", "f".repeat(64));
    expect(screen.getByText(es.load.acceptedTitle)).toBeInTheDocument();
    expect(screen.getByText(es.datasets.lineas_produccion.removedRows)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: es.load.draft })).toHaveAttribute(
      "href",
      draftPath(DRAFT),
    );
    expect(screen.getByRole("link", { name: es.load.back })).toHaveAttribute("href", paths.history);
  });

  it("from the command line, says the app did not validate it", async () => {
    stubApi({
      answers: {
        [`/api/loads/${CLI_RUN}`]: () =>
          json(
            detailOf({
              ...fromCli,
              sheets: [{ name: "Datos", dataset_key: null, rows: 14458 }],
              validation: null,
              difference: null,
              draft_id: null,
              draft_available: false,
            }),
          ),
      },
    });
    renderAppAt(loadPath(CLI_RUN));

    expect(await screen.findByText(es.load.cliNote)).toBeInTheDocument();
    expect(screen.queryByText(es.load.validationTitle)).toBeNull();
    expect(screen.queryByRole("link", { name: es.load.draft })).toBeNull();
  });

  it("downloads the original through the API, with the name it was uploaded with", async () => {
    const user = userEvent.setup();
    const saved: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      saved.push(this.download);
    });
    vi.stubGlobal("URL", { ...URL, createObjectURL: () => "blob:test", revokeObjectURL: () => {} });
    const fetchStub = stubApi({
      answers: {
        [`/api/loads/${APP_RUN}`]: () => json(detailOf()),
        [`/api/loads/${APP_RUN}/original`]: () =>
          new Response(new Uint8Array([80, 75, 3, 4]), {
            headers: {
              "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              "Content-Length": "4",
              "Content-Disposition":
                "attachment; filename=\"L_neas.xlsx\"; filename*=UTF-8''L%C3%ADneas.xlsx",
            },
          }),
      },
    });
    renderAppAt(loadPath(APP_RUN));

    await user.click(await screen.findByRole("button", { name: es.load.download }));

    await waitFor(() => expect(saved).toEqual(["Líneas.xlsx"]));
    const download = calls(fetchStub).find((call) => call.address.endsWith("/original"));
    expect(download?.headers.get("Authorization")).toMatch(/^Bearer /);
  });

  it("says when the load does not exist or is not of the person's plant", async () => {
    stubApi({ answers: { [`/api/loads/${APP_RUN}`]: () => json({ code: "unknown_load" }, 404) } });
    renderAppAt(loadPath(APP_RUN));

    expect(await screen.findByText(es.load.missing)).toBeInTheDocument();
  });
});
