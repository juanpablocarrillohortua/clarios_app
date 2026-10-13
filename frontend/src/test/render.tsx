// Test helpers shared by the test files. A test renders the whole app with a
// stand-in for sign-in (fakeAuth) and for the API (stubApi), so nothing leaves
// the test: no Microsoft, no network.
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { type Mock, vi } from "vitest";
import { App } from "../App";
import type { AuthConfig } from "../api/config";
import type { Me } from "../api/me";
import type { Account, AuthClient, CreateAuthClient } from "../auth/authClient";

/** The token the stand-in for sign-in hands out. */
export const TEST_TOKEN = "test-access-token";

export const ada: Account = { name: "Ada Lovelace", username: "ada@example.test" };

// Made-up identifiers: no test may depend on a real tenant.
const authConfig: AuthConfig = {
  tenant_id: "11111111-1111-4111-8111-111111111111",
  client_id: "33333333-3333-4333-8333-333333333333",
  authority: "https://login.example.test/11111111-1111-4111-8111-111111111111",
  api_scope: "api://33333333-3333-4333-8333-333333333333/access_as_user",
};

function person(roles: string[], permissions: string[]): Me {
  return {
    subject: "00000000-0000-4000-8000-00000000abcd",
    name: ada.name,
    username: ada.username,
    roles,
    permissions,
    plant_scope: ["1515"],
  };
}

// What GET /api/me answers for each kind of user. The permissions are written
// out because the app must take them as given: it holds no rule that derives
// them from a role.
export const viewer = person(["viewer"], ["dashboard:view"]);
export const operator = person(["operator"], ["dashboard:view", "loads:write"]);
export const noRole = person([], []);

export interface FakeAuthClient extends AuthClient {
  signIn: Mock<AuthClient["signIn"]>;
  signOut: Mock<AuthClient["signOut"]>;
  getApiToken: Mock<AuthClient["getApiToken"]>;
}

/**
 * A stand-in for the sign-in client. Ada is signed in unless `account` is
 * null. Signing in or out never completes, as in the browser, where the page
 * leaves for Microsoft.
 */
export function fakeAuth(
  state: { account?: Account | null; signInError?: string | null } = {},
): FakeAuthClient {
  const leaving = () => new Promise<never>(() => {});
  return {
    account: state.account === undefined ? ada : state.account,
    signInError: state.signInError ?? null,
    signIn: vi.fn<AuthClient["signIn"]>(leaving),
    signOut: vi.fn<AuthClient["signOut"]>(leaving),
    getApiToken: vi.fn<AuthClient["getApiToken"]>(async () => TEST_TOKEN),
  };
}

interface RenderOptions {
  /** Who is signed in. Defaults to Ada, signed in. */
  auth?: FakeAuthClient;
  /** Replaces the building of the sign-in client, for example to make it fail. */
  createAuthClient?: CreateAuthClient;
}

/** Render the whole app as if the browser had opened `path`. */
export function renderAppAt(path: string, options: RenderOptions = {}) {
  const auth = options.auth ?? fakeAuth();
  const createAuthClient = vi.fn<CreateAuthClient>(options.createAuthClient ?? (async () => auth));
  render(
    <MemoryRouter initialEntries={[path]}>
      <App createAuthClient={createAuthClient} />
    </MemoryRouter>,
    // As main.tsx renders it: React then runs every effect twice, which is
    // what a "sign in once" guard has to survive.
    { reactStrictMode: true },
  );
  return { auth, createAuthClient };
}

/** A JSON answer such as the API gives. */
export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Answer = () => Response | Promise<Response>;

interface ApiStub {
  /** Who GET /api/me says is signed in. Defaults to an operator. */
  me?: Me;
  /** The version GET /api/health reports. */
  version?: string;
  /** Replaces the answer to one address, for example to make it fail. */
  answers?: Record<string, Answer>;
}

/** Make the API answer as the real one does, for the three calls the shell makes. */
export function stubApi({ me = operator, version = "0.0.0-test", answers = {} }: ApiStub = {}) {
  const usual: Record<string, Answer> = {
    "/api/config": () => json({ auth: authConfig }),
    "/api/me": () => json(me),
    "/api/health": () =>
      json({ status: "ok", service: "clarios-energy", version, environment: "test" }),
  };
  const fetchStub = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const address = String(input);
    const answer = answers[address] ?? usual[address];
    return answer ? answer() : json({ code: "not_found" }, 404);
  });
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}
