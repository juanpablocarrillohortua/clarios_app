// The footer line that reports whether the API answers.
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { fetchHealth } from "../api/health";
import es from "../i18n/es.json";
import { paths } from "../paths";
import { renderAppAt, stubApi } from "../test/render";

/** The API answers everything as usual, except the health check. */
function stubHealth(answer: () => Response | Promise<Response>) {
  return stubApi({ answers: { "/api/health": answer } });
}

describe("the API status line", () => {
  it("shows the version the API reports", async () => {
    const fetchStub = stubApi({ version: "1.2.3" });
    renderAppAt(paths.dashboard);

    expect(await screen.findByText(/1\.2\.3/)).toHaveTextContent(
      es.api.online.replace("{{version}}", "1.2.3"),
    );
    expect(fetchStub).toHaveBeenCalledWith("/api/health", expect.anything());
  });

  it("says it is checking until the API answers", async () => {
    stubHealth(() => new Promise<Response>(() => {}));
    renderAppAt(paths.dashboard);

    expect(await screen.findByText(es.api.checking)).toHaveRole("status");
  });

  it("says the service is unreachable when the request fails", async () => {
    stubHealth(() => {
      throw new TypeError("network down");
    });
    renderAppAt(paths.dashboard);

    expect(await screen.findByText(es.api.offline)).toBeInTheDocument();
  });

  it("says the service is unreachable when the API answers an error", async () => {
    stubHealth(() => new Response("bad gateway", { status: 502 }));
    renderAppAt(paths.dashboard);

    expect(await screen.findByText(es.api.offline)).toBeInTheDocument();
  });

  it("says the service is unreachable when the answer is a page, not JSON", async () => {
    // What a proxy that routes /api to the app itself would return.
    stubHealth(() => new Response("<!doctype html>", { status: 200 }));
    renderAppAt(paths.dashboard);

    expect(await screen.findByText(es.api.offline)).toBeInTheDocument();
  });

  it("asks without the user's token: the health check is public", async () => {
    const fetchStub = stubApi();
    renderAppAt(paths.dashboard);

    await screen.findByText(/0\.0\.0-test/);
    const [, request] = fetchStub.mock.calls.find(([address]) => address === "/api/health") ?? [];

    expect(request?.headers).toEqual({ Accept: "application/json" });
  });
});

describe("fetchHealth", () => {
  it("names the status when the API answers an error", async () => {
    vi.stubGlobal("fetch", async () => new Response("", { status: 503 }));

    await expect(fetchHealth()).rejects.toThrow("503");
  });
});
