// The loading page, from choosing a dataset to the upload on its way: the
// template, the checks made before anything is sent, the progress, the cancel,
// and what the server's refusals read as. The server is played by stubApi()
// and the upload by a stand-in for XMLHttpRequest (test/xhr.ts).
import { act, configure, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import i18n from "../i18n";
import es from "../i18n/es.json";
import { paths } from "../paths";
import {
  calls,
  datasetsAnswer,
  json,
  onWideScreen,
  renderAppAt,
  stubApi,
  TEST_TOKEN,
} from "../test/render";
import { fakeXhr, fileOf } from "../test/xhr";

const MIB = 1024 * 1024;
const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

afterEach(() => {
  vi.unstubAllGlobals();
});

// These pages make several requests before they settle, and the whole suite
// runs at once: a second is too short a wait when the machine is busy.
configure({ asyncUtilTimeout: 5000 });

async function chooseLines() {
  const user = userEvent.setup();
  const card = await screen.findByRole("radio", {
    name: new RegExp(es.datasets.lineas_produccion.name),
  });
  await user.click(card);
  return user;
}

function pick(file: File) {
  fireEvent.change(screen.getByTestId("file-input"), { target: { files: [file] } });
}

describe("choosing what to load", () => {
  it("offers every dataset the server takes from a file, by its name", async () => {
    stubApi();
    renderAppAt(paths.loads);

    const choices = await screen.findAllByRole("radio");
    expect(choices).toHaveLength(2);
    expect(screen.getByText(es.datasets.lineas_produccion.name)).toBeInTheDocument();
    expect(screen.getByText(es.datasets.qad_facturas.description)).toBeInTheDocument();
    expect(screen.getByText(es.datasets.kind.movement)).toBeInTheDocument();
    expect(screen.getByText(es.loads.intro)).toBeInTheDocument();
  });

  it("asks for a file only once a dataset is chosen", async () => {
    stubApi();
    renderAppAt(paths.loads);
    await screen.findAllByRole("radio");

    expect(screen.queryByText(es.loads.file.heading)).toBeNull();
    await chooseLines();

    expect(screen.getByText(es.loads.file.heading)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: es.loads.dataset.template })).toBeInTheDocument();
  });

  it("downloads the chosen dataset's template with the user's token", async () => {
    const fetchStub = stubApi({
      answers: {
        "/api/datasets/lineas_produccion/template": () =>
          new Response(new Blob(["PK"]), {
            headers: {
              "Content-Type": XLSX,
              "Content-Disposition": 'attachment; filename="lineas_produccion.xlsx"',
            },
          }),
      },
    });
    const createObjectURL = vi.fn(() => "blob:template");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderAppAt(paths.loads);
    const user = await chooseLines();

    await user.click(screen.getByRole("button", { name: es.loads.dataset.template }));

    await waitFor(() => expect(click).toHaveBeenCalledOnce());
    const download = calls(fetchStub).find((call) => call.address.endsWith("/template"));
    expect(download?.headers.get("Authorization")).toBe(`Bearer ${TEST_TOKEN}`);
    expect(click.mock.contexts[0]).toHaveProperty("download", "lineas_produccion.xlsx");
  });

  it("says so when the list of datasets cannot be read, and retries", async () => {
    let failing = true;
    stubApi({
      answers: {
        "/api/datasets": () => (failing ? json({ code: "boom" }, 500) : json(datasetsAnswer)),
      },
    });
    renderAppAt(paths.loads);

    const user = userEvent.setup();
    expect(
      await screen.findByText(es.loads.dataset.failed, {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    failing = false;
    await user.click(screen.getByRole("button", { name: es.problem.retry }));

    expect(await screen.findAllByRole("radio")).toHaveLength(2);
  });
});

describe("checking a file before sending it", () => {
  it.each([
    ["libro.csv", 10, i18n.t("loads.file.notXlsx")],
    ["vacio.xlsx", 0, i18n.t("loads.file.empty")],
    ["enorme.xlsx", 301 * MIB, i18n.t("loads.file.tooLarge", { size: "301 MB", max: "300 MB" })],
  ])("refuses %s at once, sending nothing", async (name, size, message) => {
    const opened = fakeXhr();
    stubApi();
    renderAppAt(paths.loads);
    await chooseLines();

    pick(fileOf(name, size));

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByRole("button", { name: es.loads.file.upload })).toBeNull();
    expect(opened).toHaveLength(0);
  });

  it("says before sending that a file over 5 MB is kept but not validated yet", async () => {
    stubApi();
    renderAppAt(paths.loads);
    await chooseLines();

    pick(fileOf("grande.xlsx", 12 * MIB));

    expect(
      await screen.findByText(i18n.t("loads.file.large", { limit: "5 MB" })),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: es.loads.file.uploadOnly })).toBeInTheDocument();
  });

  it("takes a file dropped on the zone, lit while it is over it", async () => {
    onWideScreen();
    stubApi();
    renderAppAt(paths.loads);
    await chooseLines();
    const zone = screen.getByRole("button", { name: new RegExp(es.loads.file.drop) });

    fireEvent.dragEnter(zone);
    expect(zone).toHaveAttribute("data-dragging");
    expect(zone).toHaveTextContent(es.loads.file.dropping);
    fireEvent.drop(zone, { dataTransfer: { files: [fileOf("Líneas.xlsx", 2048)] } });

    expect(zone).not.toHaveAttribute("data-dragging");
    expect(await screen.findByText("Líneas.xlsx")).toBeInTheDocument();
  });

  it("is a plain button on a phone, where nothing is dragged", async () => {
    stubApi();
    renderAppAt(paths.loads);
    await chooseLines();

    expect(
      screen.getByRole("button", { name: new RegExp(es.loads.file.choose) }),
    ).toBeInTheDocument();
    expect(screen.queryByText(es.loads.file.drop)).toBeNull();
  });
});

describe("uploading", () => {
  async function startUpload(size = 2 * MIB) {
    const opened = fakeXhr();
    const fetchStub = stubApi();
    renderAppAt(paths.loads);
    const user = await chooseLines();
    pick(fileOf("Líneas de producción.xlsx", size, XLSX));
    await user.click(await screen.findByRole("button", { name: es.loads.file.upload }));
    await waitFor(() => expect(opened).toHaveLength(1));
    const [request] = opened;
    if (!request) throw new Error("no upload was opened");
    return { request, user, fetchStub };
  }

  it("sends the file as the body, with the token and its name", async () => {
    const { request } = await startUpload();

    expect(request.method).toBe("POST");
    expect(request.url).toBe("/api/drafts?dataset_key=lineas_produccion");
    expect(request.headers.Authorization).toBe(`Bearer ${TEST_TOKEN}`);
    expect(request.headers["X-File-Name"]).toBe(encodeURIComponent("Líneas de producción.xlsx"));
    expect(request.body).toBeInstanceOf(File);
  });

  it("shows the bytes sent as they leave, on a bar and in words", async () => {
    const { request } = await startUpload(4 * MIB);

    act(() => request.progress(1 * MIB, 4 * MIB));

    const bar = screen.getByRole("progressbar", { name: es.loads.upload.label });
    expect(bar).toHaveAttribute("aria-valuenow", "25");
    expect(
      screen.getByText(
        i18n.t("loads.upload.progress", { sent: "1 MB", total: "4 MB", percent: 25 }),
      ),
    ).toBeInTheDocument();
    // The choice cannot change under an upload.
    for (const radio of screen.getAllByRole("radio")) {
      expect(radio).toBeDisabled();
    }

    act(() => request.progress(4 * MIB, 4 * MIB));
    expect(screen.getByText(es.loads.upload.archiving)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: es.loads.upload.cancel })).toBeNull();
  });

  it("can be cancelled at any moment, keeping the file chosen", async () => {
    const { request, user } = await startUpload();
    act(() => request.progress(MIB, 2 * MIB));

    await user.click(screen.getByRole("button", { name: es.loads.upload.cancel }));

    expect(request.aborted).toBe(true);
    expect(await screen.findByText(es.loads.upload.cancelled)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: es.loads.file.upload })).toBeInTheDocument();
  });

  it.each([
    // The browser let a 2 MB file go: a 413 means the server's limit differs.
    [413, "file_too_large", i18n.t("loads.upload.tooLargeForServer")],
    [415, "unsupported_file_format", i18n.t("loads.file.notXlsx")],
    [401, "unauthenticated", i18n.t("loads.upload.sessionRefused")],
    [503, "source_unavailable", i18n.t("loads.upload.refused", { code: "source_unavailable" })],
  ])("reads a %i as what it means", async (status, code, message) => {
    const { request } = await startUpload();

    act(() => request.respond(status, { code, message: "x", details: {} }));

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
  });

  it("says so when the connection breaks", async () => {
    const { request } = await startUpload();

    act(() => request.fail());

    expect(await screen.findByRole("alert")).toHaveTextContent(es.loads.upload.failed);
  });

  it("opens the draft once stored, and validates it there once", async () => {
    const draft = {
      draft_id: "d1",
      dataset_key: "lineas_produccion",
      status: "uploaded",
      filename: "Líneas de producción.xlsx",
      declared_bytes: 2 * MIB,
      size_bytes: 2 * MIB,
      content_hash: "a".repeat(64),
      created_at: "2026-10-17T10:00:00Z",
      uploaded_at: "2026-10-17T10:00:01Z",
      expires_at: "2026-11-16T10:00:01Z",
      failure_code: null,
      validation: null,
    };
    const { request, fetchStub } = await startUpload();
    const validated = {
      ...draft,
      status: "validated",
      validation: {
        validated_at: "2026-10-17T10:00:02Z",
        sheet: "Lineas Produccion",
        header_row: 1,
        rows_read: 2,
        rows_with_errors: 0,
        rows_with_warnings: 0,
        blocking: false,
        counts: [],
      },
    };
    fetchStub.mockImplementation(async (input: RequestInfo | URL) =>
      String(input) === "/api/drafts/d1/validate" ? json(validated) : json(datasetsAnswer),
    );

    act(() => request.respond(201, draft));

    expect(await screen.findByText(es.loads.result.clean)).toBeInTheDocument();
    const validations = calls(fetchStub).filter((call) => call.address.endsWith("/validate"));
    expect(validations).toHaveLength(1);
    expect(validations[0]?.method).toBe("POST");
    expect(screen.getByText(es.loads.issues.none)).toBeInTheDocument();
    const summary = screen.getByText(es.loads.result.rowsRead).closest("div");
    expect(within(summary as HTMLElement).getByText("2")).toBeInTheDocument();
  });
});
