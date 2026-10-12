// The footer line that reports whether the API answers.
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { fetchHealth } from "../api/health";
import es from "../i18n/es.json";
import { paths } from "../paths";
import { renderAppAt, stubHealthyApi } from "../test/render";

describe("the API status line", () => {
  it("shows the version the API reports", async () => {
    const fetchStub = stubHealthyApi("1.2.3");
    renderAppAt(paths.dashboard);

    expect(await screen.findByText(/1\.2\.3/)).toHaveTextContent(
      es.api.online.replace("{{version}}", "1.2.3"),
    );
    expect(fetchStub).toHaveBeenCalledWith("/api/health", expect.anything());
  });

  it("says it is checking until the API answers", () => {
    vi.stubGlobal("fetch", () => new Promise<Response>(() => {}));
    renderAppAt(paths.dashboard);

    expect(screen.getByRole("status")).toHaveTextContent(es.api.checking);
  });

  it("says the service is unreachable when the request fails", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("network down");
    });
    renderAppAt(paths.dashboard);

    expect(await screen.findByText(es.api.offline)).toBeInTheDocument();
  });

  it("says the service is unreachable when the API answers an error", async () => {
    vi.stubGlobal("fetch", async () => new Response("bad gateway", { status: 502 }));
    renderAppAt(paths.dashboard);

    expect(await screen.findByText(es.api.offline)).toBeInTheDocument();
  });

  it("says the service is unreachable when the answer is a page, not JSON", async () => {
    // What a proxy that routes /api to the app itself would return.
    vi.stubGlobal("fetch", async () => new Response("<!doctype html>", { status: 200 }));
    renderAppAt(paths.dashboard);

    expect(await screen.findByText(es.api.offline)).toBeInTheDocument();
  });
});

describe("fetchHealth", () => {
  it("names the status when the API answers an error", async () => {
    vi.stubGlobal("fetch", async () => new Response("", { status: 503 }));

    await expect(fetchHealth()).rejects.toThrow("503");
  });
});
