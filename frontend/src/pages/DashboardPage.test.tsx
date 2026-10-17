// The dashboard section, from opening it to the report on screen, and every
// message it has for a report that cannot be shown. The three statements of
// increment 3 of docs/frontend-plan.md are here, as far as a test can make
// them: a signed-in user gets the report, the report gets a new token when its
// first one runs out, and a user without access to the workspace is told so.
//
// Microsoft is played by stand-ins: fakeAuth() for sign-in, stubApi() for
// Power BI's REST API, and fakeEmbed() for the embedding library, whose
// callbacks the tests call as Power BI's frame would.
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, onTestFinished, vi } from "vitest";

import { InteractionNeeded } from "../auth/authClient";
import es from "../i18n/es.json";
import { paths } from "../paths";
import { CHECK_EVERY_MS } from "../powerbi/tokenRenewal";
import {
  EMBED_URL,
  type FakeEmbed,
  type FakeReport,
  fakeAuth,
  fakeEmbed,
  json,
  onWideScreen,
  POWER_BI_TOKEN,
  powerBiConfig,
  renderAppAt,
  stubApi,
  viewer,
} from "../test/render";
import { LOAD_TIMEOUT_MS, REVEAL_AFTER_MS } from "./DashboardPage";

const problems = es.dashboard.problem;
const REQUEST_ID = "99999999-9999-4999-8999-999999999999";

/** The report now on the page. React's strict mode draws it, removes it and draws it again. */
async function reportOnPage(embed: FakeEmbed): Promise<FakeReport> {
  await waitFor(() => expect(embed.onPage()).toHaveLength(1));
  const [report] = embed.onPage();
  if (!report) {
    throw new Error("no report is on the page");
  }
  return report;
}

/** How Power BI's REST API refuses: a status, its header, and no body. */
function refusal(status: number, info?: string) {
  const headers = new Headers({ RequestId: REQUEST_ID });
  if (info) {
    headers.set("X-PowerBI-Error-Info", info);
  }
  return () => new Response(null, { status, headers });
}

function codeLine(code: string): string {
  return problems.code.replace("{{code}}", code);
}

afterEach(() => {
  vi.useRealTimers();
});

/** A browser that offers full screen, which jsdom does not. Returns the request's stand-in. */
function offerFullscreen() {
  const requestFullscreen = vi.fn(async () => {});
  Object.defineProperty(document, "fullscreenEnabled", { configurable: true, value: true });
  Object.defineProperty(HTMLElement.prototype, "requestFullscreen", {
    configurable: true,
    value: requestFullscreen,
  });
  onTestFinished(() => {
    Reflect.deleteProperty(document, "fullscreenEnabled");
    Reflect.deleteProperty(HTMLElement.prototype, "requestFullscreen");
  });
  return requestFullscreen;
}

describe("full screen", () => {
  it("is offered in the header, and asked of the report's area", async () => {
    const user = userEvent.setup();
    onWideScreen();
    const requestFullscreen = offerFullscreen();
    stubApi();
    const { embed } = renderAppAt(paths.dashboard);
    await reportOnPage(embed);

    const header = screen.getByRole("banner");
    await user.click(within(header).getByRole("button", { name: es.dashboard.fullscreen }));

    expect(requestFullscreen).toHaveBeenCalledTimes(1);
    expect(requestFullscreen.mock.contexts[0]).toBe(screen.getByTestId("report-area"));
  });

  it("is not offered by a browser that cannot do it", async () => {
    onWideScreen();
    stubApi();
    const { embed } = renderAppAt(paths.dashboard);
    await reportOnPage(embed);

    expect(screen.queryByRole("button", { name: es.dashboard.fullscreen })).toBeNull();
  });

  it("is not offered on a narrow screen", async () => {
    offerFullscreen();
    stubApi();
    const { embed } = renderAppAt(paths.dashboard);
    await reportOnPage(embed);

    expect(screen.queryByRole("button", { name: es.dashboard.fullscreen })).toBeNull();
  });
});

describe("a signed-in user who may see the report", () => {
  it("gets the report, opened with their own Power BI token", async () => {
    const fetchStub = stubApi({ me: viewer });
    const { auth, embed } = renderAppAt(paths.dashboard);

    const report = await reportOnPage(embed);

    expect(auth.getServiceToken).toHaveBeenCalledWith(powerBiConfig.scope);
    // Power BI is asked for the report with that token, and only read from.
    const [, init] =
      fetchStub.mock.calls.find(([input]) => input === powerBiConfig.report_api_url) ?? [];
    expect(init?.headers).toEqual({
      Accept: "application/json",
      Authorization: `Bearer ${POWER_BI_TOKEN}`,
    });
    expect(init?.method).toBeUndefined();
    expect(report.options).toMatchObject({
      reportId: powerBiConfig.report_id,
      // The address Power BI answered with, not one put together here.
      embedUrl: EMBED_URL,
      token: POWER_BI_TOKEN,
      language: "es",
      title: es.dashboard.reportTitle,
    });
    expect(report.options.container).toBe(screen.getByTestId("report"));
  });

  it("is told that the report is loading until Power BI says it has loaded", async () => {
    stubApi();
    const { embed } = renderAppAt(paths.dashboard);
    const report = await reportOnPage(embed);

    expect(screen.getByText(es.dashboard.loading)).toBeInTheDocument();

    act(() => report.options.onLoaded());

    expect(screen.queryByText(es.dashboard.loading)).toBeNull();
  });

  it("keeps the report out of sight until it has loaded: Power BI's own loading page is white", async () => {
    stubApi();
    const { embed } = renderAppAt(paths.dashboard);
    const report = await reportOnPage(embed);

    expect(screen.getByTestId("report")).toHaveAttribute("data-waiting");

    act(() => report.options.onLoaded());

    expect(screen.getByTestId("report")).not.toHaveAttribute("data-waiting");
  });

  it("shows the frame after a while all the same, so a notice Power BI draws there is seen", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    stubApi();
    const { embed } = renderAppAt(paths.dashboard);
    await reportOnPage(embed);

    await act(() => vi.advanceTimersByTimeAsync(REVEAL_AFTER_MS));

    expect(screen.getByTestId("report")).not.toHaveAttribute("data-waiting");
    // Not yet called slow: that comes later.
    expect(screen.getByText(es.dashboard.loading)).toBeInTheDocument();
  });

  it("shows the whole page: the frame takes the page's shape once Power BI says it", async () => {
    stubApi();
    const { embed } = renderAppAt(paths.dashboard);
    const report = await reportOnPage(embed);
    const frame = screen.getByTestId("report-frame");

    // Until then the frame fills the area, so whatever Power BI draws has the room.
    expect(frame).not.toHaveAttribute("data-fitted");

    act(() => report.options.onPageSize?.({ width: 1280, height: 720 }));

    expect(frame).toHaveAttribute("data-fitted");
    expect(frame.style.getPropertyValue("--report-ratio")).toBe("1280 / 720");
  });

  it("can open the report in Power BI, in another tab", async () => {
    stubApi();
    renderAppAt(paths.dashboard);

    const link = await screen.findByRole("link", { name: es.dashboard.openInPowerBi });

    expect(link).toHaveAttribute("href", powerBiConfig.report_web_url);
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("has the report taken off the page on leaving the section", async () => {
    const user = userEvent.setup();
    stubApi();
    const { embed } = renderAppAt(paths.dashboard);
    const report = await reportOnPage(embed);

    await user.click(screen.getByRole("link", { name: es.nav.loads }));

    await screen.findByRole("heading", { level: 2, name: es.loads.title });
    expect(report.remove).toHaveBeenCalledTimes(1);
    expect(embed.onPage()).toHaveLength(0);
  });
});

describe("the report's token", () => {
  it("is replaced when Power BI says it has expired", async () => {
    stubApi();
    const { auth, embed } = renderAppAt(paths.dashboard);
    const report = await reportOnPage(embed);
    act(() => report.options.onLoaded());

    act(() => report.options.onError({ message: "TokenExpired", detail: null, requestId: null }));

    await waitFor(() =>
      expect(report.setToken).toHaveBeenCalledExactlyOnceWith("power-bi-token-2"),
    );
    // A new token was asked for, not the one the tab still held.
    expect(auth.getServiceToken).toHaveBeenLastCalledWith(powerBiConfig.scope, { fresh: true });
    // The report stays where it is.
    expect(embed.onPage()).toEqual([report]);
  });

  it("is handed to Power BI when its frame asks for a newer one", async () => {
    stubApi();
    const { auth, embed } = renderAppAt(paths.dashboard);
    const report = await reportOnPage(embed);

    await expect(report.options.freshToken()).resolves.toBe("power-bi-token-2");
    expect(auth.getServiceToken).toHaveBeenLastCalledWith(powerBiConfig.scope, { fresh: true });
  });

  it("has Power BI told that there is none, never handed an error", async () => {
    stubApi();
    const { auth, embed } = renderAppAt(paths.dashboard);
    const report = await reportOnPage(embed);
    auth.getServiceToken.mockRejectedValueOnce(new Error("the network is down"));

    await expect(report.options.freshToken()).resolves.toBeNull();
  });

  it("is kept fresh by the clock: on the dev server's switch, every half minute", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    stubApi();
    const { embed } = renderAppAt(`${paths.dashboard}?tokenLife=60`);
    const report = await reportOnPage(embed);

    await act(() => vi.advanceTimersByTimeAsync(CHECK_EVERY_MS));

    expect(report.setToken).toHaveBeenCalledWith("power-bi-token-2");
  });

  it("is left alone for most of its hour without that switch", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    stubApi();
    const { embed } = renderAppAt(paths.dashboard);
    const report = await reportOnPage(embed);
    act(() => report.options.onLoaded());

    await act(() => vi.advanceTimersByTimeAsync(10 * CHECK_EVERY_MS));

    expect(report.setToken).not.toHaveBeenCalled();
  });

  it("when it ran out and Microsoft needs the user, makes way for the request to continue", async () => {
    stubApi();
    const { auth, embed } = renderAppAt(paths.dashboard);
    const report = await reportOnPage(embed);
    act(() => report.options.onLoaded());
    auth.getServiceToken.mockRejectedValueOnce(new InteractionNeeded("login_required"));

    act(() => report.options.onError({ message: "TokenExpired", detail: null, requestId: null }));

    expect(await screen.findByText(problems.authorize.title)).toBeInTheDocument();
    expect(screen.getByText(codeLine("login_required"))).toBeInTheDocument();
    expect(report.remove).toHaveBeenCalledTimes(1);
  });
});

describe("a user Microsoft must hear from first", () => {
  it("is asked, and goes to Microsoft only on pressing the button", async () => {
    const user = userEvent.setup();
    stubApi();
    const auth = fakeAuth();
    auth.getServiceToken.mockRejectedValue(new InteractionNeeded("consent_required"));
    const { embed } = renderAppAt(paths.dashboard, { auth });

    expect(await screen.findByText(problems.authorize.title)).toBeInTheDocument();
    expect(screen.getByText(problems.authorize.text)).toBeInTheDocument();
    expect(screen.getByText(codeLine("consent_required"))).toBeInTheDocument();
    // Nothing happens by itself: a consent that cannot be given must not loop.
    expect(auth.authorizeService).not.toHaveBeenCalled();
    expect(embed.embedReport).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: problems.authorize.action }));

    expect(auth.authorizeService).toHaveBeenCalledExactlyOnceWith(powerBiConfig.scope);
  });

  it("sees Microsoft's own answer after coming back without the consent", async () => {
    stubApi();
    const auth = fakeAuth({ signInError: "access_denied" });
    auth.getServiceToken.mockRejectedValue(new InteractionNeeded("consent_required"));
    renderAppAt(paths.dashboard, { auth });

    expect(await screen.findByText(codeLine("access_denied"))).toBeInTheDocument();
    expect(auth.authorizeService).not.toHaveBeenCalled();
  });

  it("is told when the token cannot be had for another reason", async () => {
    stubApi();
    const auth = fakeAuth();
    auth.getServiceToken.mockRejectedValue(new Error("the network is down"));
    renderAppAt(paths.dashboard, { auth });

    expect(await screen.findByText(problems.token.title)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: es.problem.retry })).toBeInTheDocument();
    expect(auth.authorizeService).not.toHaveBeenCalled();
  });
});

describe("a user Power BI refuses", () => {
  it("without access to the workspace is told so, and what to do", async () => {
    stubApi({ answers: { [powerBiConfig.report_api_url]: refusal(401, "GroupNotAccessible") } });
    const { embed } = renderAppAt(paths.dashboard);

    expect(await screen.findByText(problems.noAccess.title)).toBeInTheDocument();
    expect(screen.getByText(problems.noAccess.text)).toBeInTheDocument();
    // Someone just given access must open Power BI once: the way there is offered.
    expect(screen.getByText(problems.noAccess.hint, { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: problems.noAccess.open })).toHaveAttribute(
      "href",
      powerBiConfig.report_web_url,
    );
    // What to quote to whoever looks into it.
    expect(screen.getByText(codeLine("GroupNotAccessible"))).toBeInTheDocument();
    expect(screen.getByText(problems.requestId.replace("{{id}}", REQUEST_ID))).toBeInTheDocument();
    expect(embed.embedReport).not.toHaveBeenCalled();
  });

  it("keeps the rest of the app in reach", async () => {
    const user = userEvent.setup();
    stubApi({ answers: { [powerBiConfig.report_api_url]: refusal(401, "GroupNotAccessible") } });
    renderAppAt(paths.dashboard);
    await screen.findByText(problems.noAccess.title);

    const navigation = screen.getByRole("navigation", { name: es.nav.label });
    await user.click(within(navigation).getByRole("link", { name: es.nav.loads }));

    expect(
      await screen.findByRole("heading", { level: 2, name: es.loads.title }),
    ).toBeInTheDocument();
  });

  it("gets the report on trying again once access has been given", async () => {
    const user = userEvent.setup();
    let allowed = false;
    stubApi({
      answers: {
        [powerBiConfig.report_api_url]: () =>
          allowed ? json({ embedUrl: EMBED_URL }) : refusal(401, "GroupNotAccessible")(),
      },
    });
    const { embed } = renderAppAt(paths.dashboard);
    await screen.findByText(problems.noAccess.title);

    allowed = true;
    await user.click(screen.getByRole("button", { name: es.problem.retry }));

    const report = await reportOnPage(embed);
    expect(report.options.embedUrl).toBe(EMBED_URL);
    expect(screen.queryByText(problems.noAccess.title)).toBeNull();
  });

  it("for a reason it does not name is told the two things to have checked", async () => {
    // How Power BI refuses a user without a licence is not known yet: until it
    // is, that user lands here, and the message names the licence.
    stubApi({ answers: { [powerBiConfig.report_api_url]: refusal(403) } });
    renderAppAt(paths.dashboard);

    expect(await screen.findByText(problems.refused.title)).toBeInTheDocument();
    expect(screen.getByText(problems.refused.text)).toBeInTheDocument();
    expect(screen.getByText(codeLine("403"))).toBeInTheDocument();
  });

  it("because the token was not accepted is told to try again", async () => {
    stubApi({ answers: { [powerBiConfig.report_api_url]: refusal(403, "InvalidToken") } });
    renderAppAt(paths.dashboard);

    expect(await screen.findByText(problems.token.title)).toBeInTheDocument();
    expect(screen.getByText(codeLine("InvalidToken"))).toBeInTheDocument();
  });
});

describe("a report that cannot be shown to anyone", () => {
  it("says so when the server names no report, and asks Microsoft for nothing", async () => {
    stubApi({ powerBi: null });
    const { auth, embed } = renderAppAt(paths.dashboard);

    expect(await screen.findByText(problems.notConfigured.title)).toBeInTheDocument();
    expect(screen.getByText(problems.notConfigured.text)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: es.dashboard.openInPowerBi })).toBeNull();
    expect(screen.queryByRole("button", { name: es.problem.retry })).toBeNull();
    expect(auth.getServiceToken).not.toHaveBeenCalled();
    expect(embed.embedReport).not.toHaveBeenCalled();
    // The rest of the app is there.
    expect(screen.getByRole("navigation", { name: es.nav.label })).toBeInTheDocument();
  });

  it("says so when Power BI has no such report, which asking again cannot change", async () => {
    stubApi({
      answers: {
        [powerBiConfig.report_api_url]: () =>
          json({ error: { code: "PowerBIEntityNotFound" } }, 404),
      },
    });
    renderAppAt(paths.dashboard);

    expect(await screen.findByText(problems.notFound.title)).toBeInTheDocument();
    expect(screen.getByText(codeLine("PowerBIEntityNotFound"))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: es.problem.retry })).toBeNull();
  });

  it("says so when Power BI does not answer", async () => {
    stubApi({
      answers: {
        [powerBiConfig.report_api_url]: () => {
          throw new TypeError("Failed to fetch");
        },
      },
    });
    renderAppAt(paths.dashboard);

    expect(await screen.findByText(problems.unreachable.title)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: es.problem.retry })).toBeInTheDocument();
  });

  it("says that Power BI is slow when the frame never reports that the report loaded, and leaves the frame in view", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    stubApi();
    const { embed } = renderAppAt(paths.dashboard);
    const report = await reportOnPage(embed);

    await act(() => vi.advanceTimersByTimeAsync(LOAD_TIMEOUT_MS));

    expect(await screen.findByText(es.dashboard.slow)).toBeInTheDocument();
    expect(screen.queryByText(es.dashboard.loading)).toBeNull();
    // Whatever Power BI drew in its frame meanwhile is still there to read.
    expect(embed.onPage()).toEqual([report]);
    expect(screen.getByTestId("report")).not.toHaveAttribute("data-waiting");
    expect(report.options.container).toBe(screen.getByTestId("report"));
    expect(screen.queryByText(problems.unreachable.title)).toBeNull();
  });

  it("drops that notice when the report loads after all", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    stubApi();
    const { embed } = renderAppAt(paths.dashboard);
    const report = await reportOnPage(embed);
    await act(() => vi.advanceTimersByTimeAsync(LOAD_TIMEOUT_MS));
    expect(await screen.findByText(es.dashboard.slow)).toBeInTheDocument();

    act(() => report.options.onLoaded());

    expect(screen.queryByText(es.dashboard.slow)).toBeNull();
    expect(embed.onPage()).toEqual([report]);
  });

  it("starts over when the user asks again from that notice", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    stubApi();
    const { auth, embed } = renderAppAt(paths.dashboard);
    const report = await reportOnPage(embed);
    await act(() => vi.advanceTimersByTimeAsync(LOAD_TIMEOUT_MS));
    const asked = auth.getServiceToken.mock.calls.length;

    await userEvent
      .setup({ advanceTimers: vi.advanceTimersByTime })
      .click(await screen.findByRole("button", { name: es.problem.retry }));

    await waitFor(() => expect(report.remove).toHaveBeenCalledTimes(1));
    const again = await reportOnPage(embed);
    expect(again).not.toBe(report);
    expect(auth.getServiceToken.mock.calls.length).toBe(asked + 1);
    expect(screen.queryByText(es.dashboard.slow)).toBeNull();
  });

  it("is not called slow once it has loaded, however long it stays open", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    stubApi();
    const { embed } = renderAppAt(paths.dashboard);
    const report = await reportOnPage(embed);
    act(() => report.options.onLoaded());

    await act(() => vi.advanceTimersByTimeAsync(2 * LOAD_TIMEOUT_MS));

    expect(screen.queryByText(es.dashboard.slow)).toBeNull();
    expect(embed.onPage()).toEqual([report]);
  });

  it.each([
    ["LoadReportFailed", problems.token.title],
    ["PowerBINotAuthorizedException", problems.noAccess.title],
    ["SomethingNotSeenYet", problems.failed.title],
  ])(
    "gives the message for a frame's error %s before the report loaded",
    async (message, title) => {
      stubApi();
      const { embed } = renderAppAt(paths.dashboard);
      const report = await reportOnPage(embed);

      act(() => report.options.onError({ message, detail: null, requestId: REQUEST_ID }));

      expect(await screen.findByText(title)).toBeInTheDocument();
      expect(screen.getByText(codeLine(message))).toBeInTheDocument();
      expect(report.remove).toHaveBeenCalledTimes(1);
    },
  );

  it("quotes Power BI's status and sentence beside the name of a frame's error", async () => {
    stubApi();
    const { embed } = renderAppAt(paths.dashboard);
    const report = await reportOnPage(embed);
    const error = {
      message: "LoadReportFailed",
      detail: "403 · Fail to initialize - Could not resolve cluster",
      requestId: REQUEST_ID,
    };

    // Power BI sent this one twice in the same instant (seen on 2026-10-13).
    act(() => {
      report.options.onError(error);
      report.options.onError(error);
    });

    expect(await screen.findByText(problems.token.title)).toBeInTheDocument();
    expect(
      screen.getByText(
        codeLine("LoadReportFailed (403 · Fail to initialize - Could not resolve cluster)"),
      ),
    ).toBeInTheDocument();
    expect(report.remove).toHaveBeenCalledTimes(1);
  });

  it("stays on screen when one visual fails after it loaded: Power BI shows that itself", async () => {
    stubApi();
    const { embed } = renderAppAt(paths.dashboard);
    const report = await reportOnPage(embed);
    act(() => report.options.onLoaded());

    act(() =>
      report.options.onError({ message: "OpenConnectionError", detail: null, requestId: null }),
    );

    expect(screen.queryByText(problems.failed.title)).toBeNull();
    expect(embed.onPage()).toEqual([report]);
  });

  it("says so when the library refuses the address it was given", async () => {
    stubApi();
    const embed = fakeEmbed();
    embed.embedReport.mockImplementation(() => {
      throw new Error("Invalid embed URL");
    });
    renderAppAt(paths.dashboard, { embed });

    expect(await screen.findByText(problems.failed.title)).toBeInTheDocument();
    expect(screen.getByText(codeLine("embed_refused"))).toBeInTheDocument();
  });

  it("says so when the embedding library could not be fetched", async () => {
    stubApi();
    renderAppAt(paths.dashboard, {
      loadEmbedReport: async () => {
        throw new Error("Failed to fetch dynamically imported module");
      },
    });

    expect(await screen.findByText(problems.failed.title)).toBeInTheDocument();
    expect(screen.getByText(codeLine("library_not_loaded"))).toBeInTheDocument();
  });
});
