// Test helpers shared by the test files. A test renders the whole app with a
// stand-in for sign-in (fakeAuth), for the API and Power BI's REST API
// (stubApi) and for the embedding library (fakeEmbed), so nothing leaves the
// test: no Microsoft, no network.
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { type Mock, vi } from "vitest";
import { App } from "../App";
import type { AuthConfig, PowerBiConfig } from "../api/config";
import type { Me } from "../api/me";
import type { Account, AuthClient, CreateAuthClient } from "../auth/authClient";
import type {
  EmbeddedReport,
  EmbedReport,
  LoadEmbedReport,
  ReportEmbedOptions,
} from "../powerbi/reportEmbed";

/** The token the stand-in for sign-in hands out for the API. */
export const TEST_TOKEN = "test-access-token";
/** The first token it hands out for Power BI; later ones are numbered. */
export const POWER_BI_TOKEN = "power-bi-token-1";
/** How long each of those lasts, as a real one does. */
export const POWER_BI_TOKEN_LIFE_MS = 60 * 60_000;

export const ada: Account = { name: "Ada Lovelace", username: "ada@example.test" };

// Made-up identifiers: no test may depend on a real tenant.
const authConfig: AuthConfig = {
  tenant_id: "11111111-1111-4111-8111-111111111111",
  client_id: "33333333-3333-4333-8333-333333333333",
  authority: "https://login.example.test/11111111-1111-4111-8111-111111111111",
  api_scope: "api://33333333-3333-4333-8333-333333333333/access_as_user",
};

// A made-up report in a made-up workspace, at addresses that are nobody's.
export const powerBiConfig: PowerBiConfig = {
  workspace_id: "44444444-4444-4444-8444-444444444444",
  report_id: "55555555-5555-4555-8555-555555555555",
  scope: "https://powerbi.example.test/Report.Read.All",
  report_api_url:
    "https://api.powerbi.example.test/groups/44444444-4444-4444-8444-444444444444/reports/55555555-5555-4555-8555-555555555555",
  report_web_url:
    "https://app.powerbi.example.test/groups/44444444-4444-4444-8444-444444444444/reports/55555555-5555-4555-8555-555555555555",
};

/** The embed address the stand-in for Power BI's REST API gives for that report. */
export const EMBED_URL =
  "https://app.powerbi.example.test/reportEmbed?reportId=55555555-5555-4555-8555-555555555555&config=abc";

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
  getServiceToken: Mock<AuthClient["getServiceToken"]>;
  authorizeService: Mock<AuthClient["authorizeService"]>;
}

/**
 * A stand-in for the sign-in client. Ada is signed in unless `account` is
 * null. Signing in or out never completes, as in the browser, where the page
 * leaves for Microsoft. Each token for a service is a new one, numbered, and
 * lasts an hour.
 */
export function fakeAuth(
  state: { account?: Account | null; signInError?: string | null } = {},
): FakeAuthClient {
  const leaving = () => new Promise<never>(() => {});
  let serviceTokens = 0;
  return {
    account: state.account === undefined ? ada : state.account,
    signInError: state.signInError ?? null,
    signIn: vi.fn<AuthClient["signIn"]>(leaving),
    signOut: vi.fn<AuthClient["signOut"]>(leaving),
    getApiToken: vi.fn<AuthClient["getApiToken"]>(async () => TEST_TOKEN),
    getServiceToken: vi.fn<AuthClient["getServiceToken"]>(async () => {
      serviceTokens += 1;
      return {
        token: `power-bi-token-${serviceTokens}`,
        expiresAt: new Date(Date.now() + POWER_BI_TOKEN_LIFE_MS),
      };
    }),
    authorizeService: vi.fn<AuthClient["authorizeService"]>(leaving),
  };
}

/** One report the app asked the stand-in library to draw. */
export interface FakeReport extends EmbeddedReport {
  /** What the page handed to the library, callbacks included. */
  options: ReportEmbedOptions;
  setToken: Mock<EmbeddedReport["setToken"]>;
  remove: Mock<EmbeddedReport["remove"]>;
}

export interface FakeEmbed {
  embedReport: Mock<EmbedReport>;
  /** Every report drawn so far, oldest first. */
  reports: FakeReport[];
  /** The reports still on the page. */
  onPage(): FakeReport[];
}

/**
 * A stand-in for the embedding library. It draws nothing and reports nothing
 * by itself: a test plays Power BI by calling the callbacks in `options`.
 */
export function fakeEmbed(): FakeEmbed {
  const reports: FakeReport[] = [];
  const embedReport = vi.fn<EmbedReport>((options) => {
    const report: FakeReport = {
      options,
      setToken: vi.fn<EmbeddedReport["setToken"]>(async () => {}),
      remove: vi.fn<EmbeddedReport["remove"]>(),
    };
    reports.push(report);
    return report;
  });
  return {
    embedReport,
    reports,
    onPage: () => reports.filter((report) => report.remove.mock.calls.length === 0),
  };
}

interface RenderOptions {
  /** Who is signed in. Defaults to Ada, signed in. */
  auth?: FakeAuthClient;
  /** Replaces the building of the sign-in client, for example to make it fail. */
  createAuthClient?: CreateAuthClient;
  /** The stand-in for the embedding library. */
  embed?: FakeEmbed;
  /** Replaces the fetching of the embedding library, for example to make it fail. */
  loadEmbedReport?: LoadEmbedReport;
}

/** Render the whole app as if the browser had opened `path`. */
export function renderAppAt(path: string, options: RenderOptions = {}) {
  const auth = options.auth ?? fakeAuth();
  const embed = options.embed ?? fakeEmbed();
  const createAuthClient = vi.fn<CreateAuthClient>(options.createAuthClient ?? (async () => auth));
  const loadEmbedReport = options.loadEmbedReport ?? (async () => embed.embedReport);
  render(
    <MemoryRouter initialEntries={[path]}>
      <App createAuthClient={createAuthClient} loadEmbedReport={loadEmbedReport} />
    </MemoryRouter>,
    // As main.tsx renders it: React then runs every effect twice, which is
    // what a "sign in once" guard has to survive.
    { reactStrictMode: true },
  );
  return { auth, createAuthClient, embed };
}

/**
 * Make the window a desktop's: every `min-width` query matches, so the shell
 * draws its wide layout. Without this a test sees the narrow one, because
 * test/setup.ts answers every query with "no".
 */
export function onWideScreen() {
  const answer = window.matchMedia;
  vi.stubGlobal("matchMedia", (query: string) => ({
    ...answer(query),
    matches: query.includes("min-width"),
  }));
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
  /** The report GET /api/config names; null for a server that names none. */
  powerBi?: PowerBiConfig | null;
  /** Replaces the answer to one address, for example to make it fail. */
  answers?: Record<string, Answer>;
}

/**
 * Make the API answer as the real one does, for the three calls the shell
 * makes, and Power BI's REST API for the one the dashboard makes.
 */
export function stubApi({
  me = operator,
  version = "0.0.0-test",
  powerBi = powerBiConfig,
  answers = {},
}: ApiStub = {}) {
  const usual: Record<string, Answer> = {
    "/api/config": () => json({ auth: authConfig, powerbi: powerBi }),
    "/api/me": () => json(me),
    "/api/health": () =>
      json({ status: "ok", service: "clarios-energy", version, environment: "test" }),
    [powerBiConfig.report_api_url]: () => json({ embedUrl: EMBED_URL }),
  };
  const fetchStub = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const address = String(input);
    const answer = answers[address] ?? usual[address];
    return answer ? answer() : json({ code: "not_found" }, 404);
  });
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}
