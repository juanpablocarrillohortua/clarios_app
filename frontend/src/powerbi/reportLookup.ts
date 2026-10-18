// Asks Power BI for the report before it is embedded, with the user's own
// token. Two things come of it:
//
//   - the address to embed the report from, which only Power BI knows whole;
//   - when Power BI refuses, a status and a code that say why, so the page can
//     show one message per cause.
//
// GET is the only method the app ever sends to Power BI.
import { problemFromAnswer, ReportProblem } from "./reportProblem";

export interface ReportAddress {
  /** Where Power BI's frame loads this report from. */
  embedUrl: string;
}

/** Power BI's code for a refusal, from its header or, failing that, its body. */
async function refusalCode(response: Response): Promise<string | null> {
  const header = response.headers.get("X-PowerBI-Error-Info");
  if (header) {
    return header;
  }
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null && "error" in body) {
      const { error } = body;
      if (typeof error === "object" && error !== null && "code" in error) {
        return typeof error.code === "string" && error.code ? error.code : null;
      }
    }
  } catch {
    // An empty body, which is how Power BI answers most refusals.
  }
  return null;
}

/**
 * Return where to embed the report from.
 *
 * @param url the report's address in Power BI's REST API, as GET /api/config gives it.
 * @param token the user's access token for Power BI.
 * @throws ReportProblem when Power BI refuses, fails or cannot be reached.
 */
export async function lookUpReport(url: string, token: string): Promise<ReportAddress> {
  let response: Response;
  try {
    response = await fetch(url, {
      headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
    });
  } catch {
    // No answer at all: no network, a blocked address, or a browser that was
    // not allowed to read the answer. They cannot be told apart from here.
    throw new ReportProblem("unreachable");
  }

  const requestId = response.headers.get("RequestId");
  if (!response.ok) {
    throw problemFromAnswer(response.status, await refusalCode(response), requestId);
  }

  const body: unknown = await response.json().catch(() => null);
  if (typeof body === "object" && body !== null && "embedUrl" in body) {
    const { embedUrl } = body;
    if (typeof embedUrl === "string" && embedUrl) {
      return { embedUrl };
    }
  }
  throw new ReportProblem("failed", "no_embed_url", requestId);
}
