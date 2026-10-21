// What each kind of user sees, from opening the app to the first page. The
// statement of increment 2 of docs/frontend-plan.md is here: a viewer cannot
// open data loading and an operator can.
import { cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import es from "../i18n/es.json";
import { paths } from "../paths";
import {
  ada,
  fakeAuth,
  json,
  noRole,
  operator,
  renderAppAt,
  stubApi,
  TEST_TOKEN,
  viewer,
} from "../test/render";

const signedOut = () => fakeAuth({ account: null });

function shell() {
  return screen.queryByRole("navigation", { name: es.nav.label });
}

function sectionHeading() {
  return screen.findByRole("heading", { level: 2 });
}

describe("a visitor who is not signed in", () => {
  it("is sent to Microsoft, once, and sees nothing of the app", async () => {
    const fetchStub = stubApi();
    const { auth } = renderAppAt(paths.loads, { auth: signedOut() });

    expect(await screen.findByText(es.session.redirecting)).toBeInTheDocument();
    expect(auth.signIn).toHaveBeenCalledTimes(1);
    expect(auth.signIn).toHaveBeenCalledWith();
    expect(shell()).toBeNull();
    expect(fetchStub).not.toHaveBeenCalledWith("/api/me", expect.anything());
  });

  it("is asked, not sent again, when they come back still signed out", async () => {
    const user = userEvent.setup();
    stubApi();
    // The first arrival goes to Microsoft; the visitor presses "back".
    renderAppAt(paths.dashboard, { auth: signedOut() });
    await screen.findByText(es.session.redirecting);
    cleanup();

    const { auth } = renderAppAt(paths.dashboard, { auth: signedOut() });

    expect(await screen.findByText(es.signIn.failed)).toBeInTheDocument();
    expect(auth.signIn).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: es.signIn.action }));
    expect(auth.signIn).toHaveBeenCalledExactlyOnceWith({ chooseAccount: true });
  });

  it("is told why, with Microsoft's code, when sign-in failed", async () => {
    stubApi();
    const auth = fakeAuth({ account: null, signInError: "access_denied" });
    renderAppAt(paths.dashboard, { auth });

    expect(await screen.findByText(es.signIn.failed)).toBeInTheDocument();
    expect(
      screen.getByText(es.signIn.errorCode.replace("{{code}}", "access_denied")),
    ).toBeInTheDocument();
    expect(auth.signIn).not.toHaveBeenCalled();
  });

  it("stays signed out on the page Microsoft returns to after sign-out", async () => {
    const user = userEvent.setup();
    stubApi();
    const { auth } = renderAppAt(paths.signedOut, { auth: signedOut() });

    expect(await screen.findByText(es.signIn.signedOut)).toBeInTheDocument();
    expect(auth.signIn).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: es.signIn.action }));
    expect(auth.signIn).toHaveBeenCalledExactlyOnceWith({ chooseAccount: true });
  });
});

describe("a viewer", () => {
  it("sees the dashboard and the models, and no entry for data loading", async () => {
    stubApi({ me: viewer });
    renderAppAt("/");

    expect(await sectionHeading()).toHaveTextContent(es.dashboard.title);
    const links = within(screen.getByRole("navigation", { name: es.nav.label }));
    expect(links.getAllByRole("link").map((link) => link.textContent)).toEqual([
      es.nav.dashboard,
      es.nav.models,
    ]);
  });

  it("cannot open data loading by its address", async () => {
    const user = userEvent.setup();
    stubApi({ me: viewer });
    renderAppAt(paths.loads);

    expect(await sectionHeading()).toHaveTextContent(es.forbidden.title);
    expect(screen.getByText(es.forbidden.text)).toBeInTheDocument();
    expect(screen.queryByText(es.loads.intro)).toBeNull();

    await user.click(screen.getByRole("link", { name: es.forbidden.back }));
    expect(await sectionHeading()).toHaveTextContent(es.dashboard.title);
  });
});

describe("an operator", () => {
  it("opens data loading", async () => {
    stubApi({ me: operator });
    renderAppAt(paths.loads);

    expect(await sectionHeading()).toHaveTextContent(es.loads.title);
    expect(screen.getByText(es.loads.intro)).toBeInTheDocument();
  });

  it("is taken into the app from the signed-out page once signed in", async () => {
    stubApi({ me: operator });
    renderAppAt(paths.signedOut);

    expect(await sectionHeading()).toHaveTextContent(es.dashboard.title);
  });
});

describe("a user without a role", () => {
  it("is told so, with the account to quote, and can only sign out", async () => {
    const user = userEvent.setup();
    stubApi({ me: noRole });
    const { auth } = renderAppAt(paths.dashboard);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(es.noAccess.title);
    expect(
      screen.getByText(es.noAccess.account.replace("{{account}}", ada.username)),
    ).toBeInTheDocument();
    expect(shell()).toBeNull();

    await user.click(screen.getByRole("button", { name: es.user.signOut }));
    expect(auth.signOut).toHaveBeenCalledTimes(1);
  });
});

describe("the signed-in user", () => {
  it("is named in the header, and can sign out from it", async () => {
    const user = userEvent.setup();
    stubApi();
    const { auth } = renderAppAt(paths.dashboard);

    expect(await screen.findByTestId("signed-in-as")).toHaveTextContent(ada.name);

    await user.click(screen.getByRole("button", { name: es.user.signOut }));
    expect(auth.signOut).toHaveBeenCalledTimes(1);
  });

  it("is asked about with their token, which goes nowhere else", async () => {
    const fetchStub = stubApi();
    renderAppAt(paths.dashboard);
    await sectionHeading();

    const sent = Object.fromEntries(
      fetchStub.mock.calls.map(([address, request]) => [String(address), request?.headers]),
    );

    expect(sent["/api/me"]).toEqual({
      Accept: "application/json",
      Authorization: `Bearer ${TEST_TOKEN}`,
    });
    expect(sent["/api/config"]).toEqual({ Accept: "application/json" });
  });
});

describe("when the session cannot start", () => {
  it("says that sign-in is not configured, and builds no sign-in client", async () => {
    stubApi({ answers: { "/api/config": () => json({ auth: null }) } });
    const { createAuthClient } = renderAppAt(paths.dashboard);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(
      es.problem.notConfigured.title,
    );
    expect(createAuthClient).not.toHaveBeenCalled();
  });

  it("says the service is unreachable, and tries again when asked", async () => {
    const user = userEvent.setup();
    let down = true;
    stubApi({
      answers: {
        "/api/config": () =>
          down ? new Response("bad gateway", { status: 502 }) : json({ auth: null }),
      },
    });
    renderAppAt(paths.dashboard);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(
      es.problem.unreachable.title,
    );

    down = false;
    await user.click(screen.getByRole("button", { name: es.problem.retry }));
    expect(await screen.findByText(es.problem.notConfigured.text)).toBeInTheDocument();
  });

  it("says so when the browser cannot prepare sign-in", async () => {
    stubApi();
    renderAppAt(paths.dashboard, {
      createAuthClient: async () => {
        throw new Error("crypto is not available on this address");
      },
    });

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(
      es.problem.cannotStart.title,
    );
  });

  it("offers to sign in again when the API refuses the token", async () => {
    const user = userEvent.setup();
    stubApi({ answers: { "/api/me": () => json({ code: "unauthenticated" }, 401) } });
    const { auth } = renderAppAt(paths.dashboard);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(
      es.problem.sessionRefused.title,
    );
    expect(shell()).toBeNull();

    await user.click(screen.getByRole("button", { name: es.signIn.action }));
    expect(auth.signIn).toHaveBeenCalledExactlyOnceWith({ chooseAccount: true });
  });

  it("does not take a server fault for a refusal, and recovers on retry", async () => {
    const user = userEvent.setup();
    let down = true;
    stubApi({
      answers: {
        "/api/me": () =>
          down ? json({ code: "identity_provider_unavailable" }, 503) : json(operator),
      },
    });
    const { auth } = renderAppAt(paths.dashboard);

    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(
      es.problem.unreachable.title,
    );
    expect(auth.signIn).not.toHaveBeenCalled();

    down = false;
    await user.click(screen.getByRole("button", { name: es.problem.retry }));
    expect(await sectionHeading()).toHaveTextContent(es.dashboard.title);
  });
});
