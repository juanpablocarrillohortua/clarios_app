// The Microsoft sign-in client, against a stand-in for MSAL. It checks what
// this file decides: which account is the signed-in one, what is asked of MSAL,
// and what happens when a token cannot be renewed without the user. That MSAL
// itself signs a user in is seen only in a browser.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthConfig } from "../api/config";
import { paths, REDIRECT_PAGE } from "../paths";

const msal = vi.hoisted(() => {
  class InteractionRequiredAuthError extends Error {
    errorCode = "interaction_required";
  }
  const client = {
    initialize: vi.fn(async () => {}),
    handleRedirectPromise: vi.fn(async (): Promise<{ account: unknown } | null> => null),
    getActiveAccount: vi.fn((): unknown => null),
    getAllAccounts: vi.fn((): unknown[] => []),
    setActiveAccount: vi.fn(),
    loginRedirect: vi.fn(async () => {}),
    logoutRedirect: vi.fn(async () => {}),
    acquireTokenSilent: vi.fn(
      async (): Promise<{ accessToken: string; expiresOn?: Date | null }> => ({
        accessToken: "token-from-msal",
      }),
    ),
    acquireTokenRedirect: vi.fn(async () => {}),
  };
  // A plain function, so it can be called with `new` and hand back the client.
  const PublicClientApplication = vi.fn(function PublicClientApplication() {
    return client;
  });
  return { client, PublicClientApplication, InteractionRequiredAuthError };
});

vi.mock("@azure/msal-browser", () => ({
  PublicClientApplication: msal.PublicClientApplication,
  InteractionRequiredAuthError: msal.InteractionRequiredAuthError,
}));

// Imported after the stand-in is declared; vitest moves vi.mock above it.
const { createMsalAuthClient } = await import("./msalAuthClient");
const { InteractionNeeded } = await import("./authClient");

const config: AuthConfig = {
  tenant_id: "11111111-1111-4111-8111-111111111111",
  client_id: "33333333-3333-4333-8333-333333333333",
  authority: "https://login.example.test/11111111-1111-4111-8111-111111111111",
  api_scope: "api://33333333-3333-4333-8333-333333333333/access_as_user",
};
const scopes = [config.api_scope];
// The scope of another service, as GET /api/config names Power BI's.
const reportScope = "https://powerbi.example.test/Report.Read.All";

const adaAccount = { name: "Ada Lovelace", username: "ada@example.test", homeAccountId: "ada" };
const graceAccount = { name: undefined, username: "grace@example.test", homeAccountId: "grace" };

beforeEach(() => {
  // Back to the stand-in as declared above: no calls, and no answer left
  // queued by a test that did not use it up.
  vi.resetAllMocks();
});

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("building the client", () => {
  it("gives MSAL the app registration and the two registered addresses", async () => {
    await createMsalAuthClient(config);

    expect(msal.PublicClientApplication).toHaveBeenCalledExactlyOnceWith({
      auth: {
        clientId: config.client_id,
        authority: config.authority,
        redirectUri: `${window.location.origin}${REDIRECT_PAGE}`,
        postLogoutRedirectUri: `${window.location.origin}${paths.signedOut}`,
      },
      cache: { cacheLocation: "sessionStorage" },
    });
  });

  it("reads Microsoft's answer only after MSAL is ready", async () => {
    await createMsalAuthClient(config);

    const ready = msal.client.initialize.mock.invocationCallOrder[0] ?? Number.NaN;
    const answer = msal.client.handleRedirectPromise.mock.invocationCallOrder[0] ?? Number.NaN;
    expect(ready).toBeLessThan(answer);
  });

  it("takes the account Microsoft just returned", async () => {
    msal.client.handleRedirectPromise.mockResolvedValueOnce({ account: adaAccount });
    msal.client.getAllAccounts.mockReturnValueOnce([graceAccount]);

    const client = await createMsalAuthClient(config);

    expect(client.account).toEqual({ name: "Ada Lovelace", username: "ada@example.test" });
    expect(client.signInError).toBeNull();
    expect(msal.client.setActiveAccount).toHaveBeenCalledExactlyOnceWith(adaAccount);
  });

  it("otherwise takes the account already in the tab", async () => {
    msal.client.getAllAccounts.mockReturnValueOnce([graceAccount]);

    const client = await createMsalAuthClient(config);

    // An account without a display name still has the name it signs in with.
    expect(client.account).toEqual({ name: "", username: "grace@example.test" });
  });

  it("has no account when nobody is signed in", async () => {
    const client = await createMsalAuthClient(config);

    expect(client.account).toBeNull();
    expect(client.signInError).toBeNull();
    expect(msal.client.setActiveAccount).not.toHaveBeenCalled();
  });

  it.each([
    [Object.assign(new Error("denied"), { errorCode: "access_denied" }), "access_denied"],
    [new Error("something else"), "unknown_error"],
    ["not even an error", "unknown_error"],
  ])("keeps the code of a sign-in that failed", async (failure, code) => {
    msal.client.handleRedirectPromise.mockRejectedValueOnce(failure);

    const client = await createMsalAuthClient(config);

    expect(client.account).toBeNull();
    expect(client.signInError).toBe(code);
  });

  it("removes what Microsoft adds to the address after sign-out", async () => {
    window.history.replaceState(null, "", `${paths.signedOut}?state=abc`);

    await createMsalAuthClient(config);

    expect(window.location.pathname).toBe(paths.signedOut);
    expect(window.location.search).toBe("");
  });
});

describe("signing in and out", () => {
  it("asks for the API's scope at sign-in", async () => {
    const client = await createMsalAuthClient(config);

    await client.signIn();

    expect(msal.client.loginRedirect).toHaveBeenCalledExactlyOnceWith({
      scopes,
      extraScopesToConsent: [],
      prompt: undefined,
    });
  });

  it("names the other services at sign-in, so their consent is asked on the same screen", async () => {
    const client = await createMsalAuthClient(config, { extraScopes: [reportScope] });

    await client.signIn();

    // Not among `scopes`: a token is for one service, and this one is the API's.
    expect(msal.client.loginRedirect).toHaveBeenCalledExactlyOnceWith({
      scopes,
      extraScopesToConsent: [reportScope],
      prompt: undefined,
    });
  });

  it("lets the user choose an account when asked to", async () => {
    const client = await createMsalAuthClient(config);

    await client.signIn({ chooseAccount: true });

    expect(msal.client.loginRedirect).toHaveBeenCalledExactlyOnceWith({
      scopes,
      extraScopesToConsent: [],
      prompt: "select_account",
    });
  });

  it("signs the account out of Microsoft too", async () => {
    msal.client.getAllAccounts.mockReturnValueOnce([adaAccount]);
    const client = await createMsalAuthClient(config);

    await client.signOut();

    expect(msal.client.logoutRedirect).toHaveBeenCalledExactlyOnceWith({ account: adaAccount });
  });
});

describe("the token for the API", () => {
  it("comes from MSAL, for the signed-in account and the API's scope", async () => {
    msal.client.getAllAccounts.mockReturnValueOnce([adaAccount]);
    const client = await createMsalAuthClient(config);

    await expect(client.getApiToken()).resolves.toBe("token-from-msal");
    expect(msal.client.acquireTokenSilent).toHaveBeenCalledExactlyOnceWith({
      scopes,
      account: adaAccount,
    });
  });

  it("sends the user to Microsoft when it cannot be renewed without them", async () => {
    msal.client.getAllAccounts.mockReturnValueOnce([adaAccount]);
    msal.client.acquireTokenSilent.mockRejectedValueOnce(
      new msal.InteractionRequiredAuthError("the session ended"),
    );
    const client = await createMsalAuthClient(config);

    const token = client.getApiToken();
    // The page is leaving: the caller must be left waiting, not handed an error.
    const outcome = await Promise.race([
      token.then(
        () => "answered",
        () => "failed",
      ),
      new Promise((resolve) => setTimeout(() => resolve("waiting"), 20)),
    ]);

    expect(outcome).toBe("waiting");
    expect(msal.client.acquireTokenRedirect).toHaveBeenCalledExactlyOnceWith({
      scopes,
      account: adaAccount,
    });
  });

  it("passes on any other failure", async () => {
    msal.client.getAllAccounts.mockReturnValueOnce([adaAccount]);
    msal.client.acquireTokenSilent.mockRejectedValueOnce(new Error("the network is down"));
    const client = await createMsalAuthClient(config);

    await expect(client.getApiToken()).rejects.toThrow("the network is down");
    expect(msal.client.acquireTokenRedirect).not.toHaveBeenCalled();
  });

  it("is refused when nobody is signed in", async () => {
    const client = await createMsalAuthClient(config);

    await expect(client.getApiToken()).rejects.toThrow("nobody is signed in");
    expect(msal.client.acquireTokenSilent).not.toHaveBeenCalled();
  });
});

describe("the token for another service", () => {
  const expiry = new Date("2026-10-13T15:00:00Z");

  it("comes from the same sign-in, with the time it expires", async () => {
    msal.client.getAllAccounts.mockReturnValueOnce([adaAccount]);
    msal.client.acquireTokenSilent.mockResolvedValueOnce({
      accessToken: "token-for-reports",
      expiresOn: expiry,
    });
    const client = await createMsalAuthClient(config);

    await expect(client.getServiceToken(reportScope)).resolves.toEqual({
      token: "token-for-reports",
      expiresAt: expiry,
    });
    // The service's scope alone, and the cache may answer.
    expect(msal.client.acquireTokenSilent).toHaveBeenCalledExactlyOnceWith({
      scopes: [reportScope],
      account: adaAccount,
      forceRefresh: false,
    });
  });

  it("is a new one when a fresh token is asked for", async () => {
    msal.client.getAllAccounts.mockReturnValueOnce([adaAccount]);
    const client = await createMsalAuthClient(config);

    await client.getServiceToken(reportScope, { fresh: true });

    expect(msal.client.acquireTokenSilent).toHaveBeenCalledExactlyOnceWith({
      scopes: [reportScope],
      account: adaAccount,
      forceRefresh: true,
    });
  });

  it("counts as due now when MSAL gives it no expiry", async () => {
    msal.client.getAllAccounts.mockReturnValueOnce([adaAccount]);
    msal.client.acquireTokenSilent.mockResolvedValueOnce({
      accessToken: "token-without-a-date",
      expiresOn: null,
    });
    const client = await createMsalAuthClient(config);
    const before = Date.now();

    const { expiresAt } = await client.getServiceToken(reportScope);

    expect(expiresAt.getTime()).toBeGreaterThanOrEqual(before);
    expect(expiresAt.getTime()).toBeLessThanOrEqual(Date.now());
  });

  it("says that Microsoft needs the user, and does not send the page away", async () => {
    msal.client.getAllAccounts.mockReturnValueOnce([adaAccount]);
    msal.client.acquireTokenSilent.mockRejectedValueOnce(
      Object.assign(new msal.InteractionRequiredAuthError("consent is missing"), {
        errorCode: "consent_required",
      }),
    );
    const client = await createMsalAuthClient(config);

    const failure = await client.getServiceToken(reportScope).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(InteractionNeeded);
    expect(failure).toHaveProperty("code", "consent_required");
    // The opposite of the API's token: a refusal here must not become a loop.
    expect(msal.client.acquireTokenRedirect).not.toHaveBeenCalled();
  });

  it("passes on any other failure", async () => {
    msal.client.getAllAccounts.mockReturnValueOnce([adaAccount]);
    msal.client.acquireTokenSilent.mockRejectedValueOnce(new Error("the network is down"));
    const client = await createMsalAuthClient(config);

    await expect(client.getServiceToken(reportScope)).rejects.toThrow("the network is down");
  });

  it("is refused when nobody is signed in", async () => {
    const client = await createMsalAuthClient(config);

    await expect(client.getServiceToken(reportScope)).rejects.toThrow("nobody is signed in");
    expect(msal.client.acquireTokenSilent).not.toHaveBeenCalled();
  });

  it("goes to Microsoft for that service when the user asks to", async () => {
    msal.client.getAllAccounts.mockReturnValueOnce([adaAccount]);
    const client = await createMsalAuthClient(config);

    await client.authorizeService(reportScope);

    expect(msal.client.acquireTokenRedirect).toHaveBeenCalledExactlyOnceWith({
      scopes: [reportScope],
      account: adaAccount,
    });
  });

  it("does not go to Microsoft for a service when nobody is signed in", async () => {
    const client = await createMsalAuthClient(config);

    await expect(client.authorizeService(reportScope)).rejects.toThrow("nobody is signed in");
    expect(msal.client.acquireTokenRedirect).not.toHaveBeenCalled();
  });
});
