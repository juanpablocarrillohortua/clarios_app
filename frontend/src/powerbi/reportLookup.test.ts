// Asking Power BI for the report, against a stand-in for its REST API. The
// answers below are the ones Power BI gave on 2026-10-13 (section 10 of
// docs/frontend-plan.md), with made-up identifiers; each must end in the cause
// the dashboard has a message for.
import { describe, expect, it, vi } from "vitest";

import { InteractionNeeded } from "../auth/authClient";
import { lookUpReport } from "./reportLookup";
import { problemFromFrame, problemFromTokenFailure, ReportProblem } from "./reportProblem";

const URL =
  "https://api.powerbi.example.test/groups/44444444-4444-4444-8444-444444444444/reports/55555555-5555-4555-8555-555555555555";
const EMBED_URL = "https://app.powerbi.example.test/reportEmbed?reportId=5555&config=abc";
const REQUEST_ID = "99999999-9999-4999-8999-999999999999";

interface Answer {
  status: number;
  /** Power BI's X-PowerBI-Error-Info header. */
  info?: string;
  body?: unknown;
  requestId?: string;
}

function answer({ status, info, body, requestId }: Answer): Response {
  const headers = new Headers();
  if (info) {
    headers.set("X-PowerBI-Error-Info", info);
  }
  if (requestId) {
    headers.set("RequestId", requestId);
  }
  // Power BI sends most refusals with no body at all.
  return new Response(body === undefined ? null : JSON.stringify(body), { status, headers });
}

/** No answer: what `fetch` rejects with when the request never completes. */
interface NoAnswer {
  rejects: unknown;
}

function powerBiAnswers(response: Answer | NoAnswer) {
  const fetchStub = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
    if ("rejects" in response) {
      throw response.rejects;
    }
    return answer(response);
  });
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

async function problemOf(response: Answer | NoAnswer): Promise<ReportProblem> {
  powerBiAnswers(response);
  const failure = await lookUpReport(URL, "a-token").catch((error: unknown) => error);
  expect(failure).toBeInstanceOf(ReportProblem);
  return failure as ReportProblem;
}

describe("asking Power BI for the report", () => {
  it("returns the address to embed it from", async () => {
    const fetchStub = powerBiAnswers({ status: 200, body: { id: "5555", embedUrl: EMBED_URL } });

    await expect(lookUpReport(URL, "a-token")).resolves.toEqual({ embedUrl: EMBED_URL });
    expect(fetchStub).toHaveBeenCalledExactlyOnceWith(URL, {
      headers: { Accept: "application/json", Authorization: "Bearer a-token" },
    });
  });

  it("only ever reads: it sends no method but GET", async () => {
    const fetchStub = powerBiAnswers({ status: 200, body: { embedUrl: EMBED_URL } });

    await lookUpReport(URL, "a-token");

    const init = fetchStub.mock.calls[0]?.[1];
    expect(init?.method ?? "GET").toBe("GET");
    expect(init?.body).toBeUndefined();
  });

  it("takes a workspace the user is not in as no access", async () => {
    const problem = await problemOf({
      status: 401,
      info: "GroupNotAccessible",
      requestId: REQUEST_ID,
    });

    expect(problem.kind).toBe("noAccess");
    expect(problem.code).toBe("GroupNotAccessible");
    expect(problem.requestId).toBe(REQUEST_ID);
  });

  it("takes Microsoft's other name for that refusal the same way", async () => {
    const problem = await problemOf({ status: 401, info: "PowerBINotAuthorizedException" });

    expect(problem.kind).toBe("noAccess");
  });

  it("takes a report the workspace does not have as not found", async () => {
    const problem = await problemOf({
      status: 404,
      body: { error: { code: "PowerBIEntityNotFound" } },
    });

    expect(problem.kind).toBe("notFound");
    expect(problem.code).toBe("PowerBIEntityNotFound");
  });

  it.each(["InvalidToken", "TokenExpired"])("takes %s as a token problem", async (info) => {
    const problem = await problemOf({ status: 403, info });

    expect(problem.kind).toBe("token");
    expect(problem.code).toBe(info);
  });

  it.each([
    [401, undefined],
    [403, undefined],
    // A name this app has never seen, such as the one a missing licence may get.
    [403, "SomethingNotSeenYet"],
  ])(
    "takes any other refusal (%i %s) as one that may be access or licence",
    async (status, info) => {
      const problem = await problemOf({ status, info });

      expect(problem.kind).toBe("refused");
      // Whatever Power BI said is kept for the support line.
      expect(problem.code).toBe(info ?? String(status));
    },
  );

  it.each([429, 500, 503])("takes %i as Power BI not answering", async (status) => {
    expect((await problemOf({ status })).kind).toBe("unreachable");
  });

  it("takes no answer at all as Power BI not answering", async () => {
    const problem = await problemOf({ rejects: new TypeError("Failed to fetch") });

    expect(problem.kind).toBe("unreachable");
    expect(problem.code).toBeNull();
  });

  it("takes any other answer as a failure, with its status", async () => {
    const problem = await problemOf({ status: 400 });

    expect(problem.kind).toBe("failed");
    expect(problem.code).toBe("400");
  });

  it.each([{ id: "5555" }, { embedUrl: "" }, "not an object"])(
    "takes a success without an embed address as a failure",
    async (body) => {
      const problem = await problemOf({ status: 200, body, requestId: REQUEST_ID });

      expect(problem.kind).toBe("failed");
      expect(problem.code).toBe("no_embed_url");
      expect(problem.requestId).toBe(REQUEST_ID);
    },
  );
});

describe("an error of Power BI's frame", () => {
  it.each([
    ["PowerBINotAuthorizedException", "noAccess"],
    ["PowerBIEntityNotFound", "notFound"],
    ["LoadReportFailed", "token"],
    ["SomethingElse", "failed"],
  ])("named %s means %s", (message, kind) => {
    const problem = problemFromFrame({ message, detail: null, requestId: REQUEST_ID });

    expect(problem.kind).toBe(kind);
    expect(problem.code).toBe(message);
    expect(problem.requestId).toBe(REQUEST_ID);
  });

  it("quotes Power BI's status and sentence in the code, and still goes by the name", () => {
    const problem = problemFromFrame({
      message: "LoadReportFailed",
      detail: "403 · Fail to initialize - Could not resolve cluster",
      requestId: null,
    });

    expect(problem.kind).toBe("token");
    expect(problem.code).toBe(
      "LoadReportFailed (403 · Fail to initialize - Could not resolve cluster)",
    );
  });
});

describe("a failure to obtain the token", () => {
  it("means the user must go to Microsoft, when Microsoft needs them", () => {
    const problem = problemFromTokenFailure(new InteractionNeeded("consent_required"));

    expect(problem.kind).toBe("authorize");
    expect(problem.code).toBe("consent_required");
  });

  it("is a token problem otherwise", () => {
    expect(problemFromTokenFailure(new Error("the network is down")).kind).toBe("token");
  });

  it("keeps a problem that already has its meaning", () => {
    const known = new ReportProblem("noAccess", "GroupNotAccessible");

    expect(problemFromTokenFailure(known)).toBe(known);
  });
});
