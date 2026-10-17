// The one file that uses Microsoft's embedding library, against the library
// itself. Nothing here reaches Power BI: jsdom creates the frame and loads
// nothing into it. What is checked is what this file decides: what the library
// is asked for, and that its events reach the app. That Power BI then draws
// the report is seen only in a browser.
//
// The library is the real one on purpose. It has no ES module build, so this
// is also the check that the bundler's conversion gives the app what it
// imports.
import { models, service } from "powerbi-client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { embedPowerBiReport } from "./powerBiReportEmbed";
import type { EmbeddedReport, ReportEmbedOptions } from "./reportEmbed";

// The library accepts only an address of Power BI's own, so this one is of
// that host. The identifiers in it are made up.
const EMBED_URL =
  "https://app.powerbi.com/reportEmbed?reportId=55555555-5555-4555-8555-555555555555&groupId=44444444-4444-4444-8444-444444444444";

const shown: EmbeddedReport[] = [];

function embed(overrides: Partial<ReportEmbedOptions> = {}) {
  const container = document.createElement("div");
  document.body.append(container);
  const options: ReportEmbedOptions = {
    container,
    reportId: "55555555-5555-4555-8555-555555555555",
    embedUrl: EMBED_URL,
    token: "a-power-bi-token",
    language: "es",
    title: "The report's name",
    freshToken: vi.fn(async () => "a-newer-token"),
    onLoaded: vi.fn(),
    onError: vi.fn(),
    ...overrides,
  };
  const report = embedPowerBiReport(options);
  shown.push(report);
  return { report, options, container, frame: container.querySelector("iframe") };
}

/** What Power BI's frame would tell the page. */
function frameSays(container: HTMLElement, event: string, detail?: unknown): void {
  container.dispatchEvent(new CustomEvent(event, { detail, bubbles: true }));
}

afterEach(() => {
  for (const report of shown.splice(0)) {
    report.remove();
  }
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

describe("embedding the report", () => {
  it("puts one frame in the container, at the address Power BI gave", () => {
    const { container, frame } = embed();

    expect(container.querySelectorAll("iframe")).toHaveLength(1);
    expect(frame?.getAttribute("src")).toContain(EMBED_URL);
  });

  it("gives the frame no border and a name a screen reader can say", () => {
    const { frame } = embed();

    expect(frame?.style.borderWidth).toBe("0px");
    expect(frame?.getAttribute("title")).toBe("The report's name");
  });

  it("asks Power BI to draw the report's own page background, not a transparent one", () => {
    // The report's pages are dark by design. Made transparent, they lost that
    // background and their light text stood on white (seen signed in,
    // 2026-10-16).
    const asked = vi.spyOn(service.Service.prototype, "embed");

    embed();

    expect(asked.mock.calls[0]?.[1]).toMatchObject({
      settings: { background: models.BackgroundType.Default },
    });
  });

  it("refuses an address that is not Power BI's", () => {
    expect(() => embed({ embedUrl: "https://example.test/reportEmbed?reportId=1" })).toThrow();
  });

  it("tells the app when the report has loaded", () => {
    const { container, options } = embed();

    frameSays(container, "loaded");

    expect(options.onLoaded).toHaveBeenCalledTimes(1);
  });

  it("passes on what Power BI calls an error, with its status, its sentence and the request to quote", () => {
    const { container, options } = embed();

    // What Power BI's frame sent for a text that is no token, on 2026-10-13.
    frameSays(container, "error", {
      message: "LoadReportFailed",
      detailedMessage: "Fail to initialize - Could not resolve cluster",
      errorCode: "403",
      level: 6,
      technicalDetails: { requestId: "99999999-9999-4999-8999-999999999999" },
    });

    expect(options.onError).toHaveBeenCalledExactlyOnceWith({
      message: "LoadReportFailed",
      detail: "403 · Fail to initialize - Could not resolve cluster",
      requestId: "99999999-9999-4999-8999-999999999999",
    });
  });

  it("passes on an error that has a sentence and no status", () => {
    const { container, options } = embed();

    frameSays(container, "error", {
      message: "TokenExpired",
      detailedMessage: "Access token has expired, resubmit with a new access token",
    });

    expect(options.onError).toHaveBeenCalledExactlyOnceWith({
      message: "TokenExpired",
      detail: "Access token has expired, resubmit with a new access token",
      requestId: null,
    });
  });

  it("keeps Power BI's error from the window, where it would pass for an uncaught one", () => {
    const { container, options } = embed();
    const uncaught = vi.fn();
    window.addEventListener("error", uncaught);

    try {
      frameSays(container, "error", { message: "TokenExpired" });
    } finally {
      window.removeEventListener("error", uncaught);
    }

    expect(options.onError).toHaveBeenCalledTimes(1);
    expect(uncaught).not.toHaveBeenCalled();
  });

  it("passes on an error that is a name and nothing else", () => {
    const { container, options } = embed();

    frameSays(container, "error", { message: "LoadReportFailed" });

    expect(options.onError).toHaveBeenCalledExactlyOnceWith({
      message: "LoadReportFailed",
      detail: null,
      requestId: null,
    });
  });
});

describe("taking the report off the page", () => {
  it("removes the frame and stops listening", () => {
    const { report, container, options } = embed();

    report.remove();
    frameSays(container, "loaded");
    frameSays(container, "error", { message: "TokenExpired" });

    expect(container.querySelector("iframe")).toBeNull();
    expect(options.onLoaded).not.toHaveBeenCalled();
    expect(options.onError).not.toHaveBeenCalled();
  });
});
