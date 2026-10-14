// Why the dashboard cannot show the report, as one of a few causes the page
// has a message for. The causes arrive from three places: the sign-in client,
// Power BI's REST API, and Power BI's frame. This file is where each of their
// answers is given its meaning.
import { InteractionNeeded } from "../auth/authClient";
import type { EmbedError } from "./reportEmbed";

export type ReportProblemKind =
  /** Microsoft needs the user: consent, a second factor, or an ended session. */
  | "authorize"
  /** No token for Power BI could be had, or Power BI did not accept it. */
  | "token"
  /** The user is not in the report's workspace. */
  | "noAccess"
  /** Power BI refused for a reason it did not name: access, or the licence. */
  | "refused"
  /** The workspace answers, and has no such report. */
  | "notFound"
  /** Power BI did not answer, or is failing. */
  | "unreachable"
  /** Anything else. */
  | "failed";

export class ReportProblem extends Error {
  constructor(
    readonly kind: ReportProblemKind,
    /** Power BI's or Microsoft's code for it, for support; null when none. */
    readonly code: string | null = null,
    /** The request Power BI's support can look up; null when none. */
    readonly requestId: string | null = null,
  ) {
    super(`the report cannot be shown (${kind}${code ? `, ${code}` : ""})`);
    this.name = "ReportProblem";
  }
}

// Power BI's own names for a failure. They arrive in the X-PowerBI-Error-Info
// header or the body of a REST answer, and as the message of a frame's error.
//
// How Power BI names a user without a licence is not known yet. Until it has
// been seen, that user falls under "refused", whose message names the licence.
const KIND_OF_CODE: Partial<Record<string, ReportProblemKind>> = {
  // Seen from the REST API for a workspace the caller is not in. A workspace
  // that does not exist answers the same: Power BI does not tell them apart.
  GroupNotAccessible: "noAccess",
  // The name Microsoft's troubleshooting table gives the same refusal.
  PowerBINotAuthorizedException: "noAccess",
  PowerBIEntityNotFound: "notFound",
  InvalidToken: "token",
  TokenExpired: "token",
  // A token Power BI cannot read. Seen from the frame on 2026-10-13 for a text
  // that is no token: status 403, "Fail to initialize - Could not resolve
  // cluster". The name is a general one, so the code shown with the message
  // carries the status and the sentence too (problemFromFrame, below).
  LoadReportFailed: "token",
};

/** The meaning of an answer of Power BI's REST API that is not a success. */
export function problemFromAnswer(
  status: number,
  code: string | null,
  requestId: string | null,
): ReportProblem {
  const named = code ? KIND_OF_CODE[code] : undefined;
  let kind: ReportProblemKind;
  if (named) {
    kind = named;
  } else if (status === 404) {
    kind = "notFound";
  } else if (status === 401 || status === 403) {
    kind = "refused";
  } else if (status === 429 || status >= 500) {
    kind = "unreachable";
  } else {
    kind = "failed";
  }
  return new ReportProblem(kind, code ?? String(status), requestId);
}

/**
 * The meaning of an error Power BI's frame reported before the report loaded.
 * The name decides the message. The code quotes Power BI in full, because one
 * name covers several causes and support needs to tell them apart.
 */
export function problemFromFrame(error: EmbedError): ReportProblem {
  const code = error.detail ? `${error.message} (${error.detail})` : error.message;
  return new ReportProblem(KIND_OF_CODE[error.message] ?? "failed", code, error.requestId);
}

/** The meaning of a failure to obtain the token for Power BI. */
export function problemFromTokenFailure(error: unknown): ReportProblem {
  if (error instanceof ReportProblem) {
    return error;
  }
  if (error instanceof InteractionNeeded) {
    return new ReportProblem("authorize", error.code);
  }
  return new ReportProblem("token");
}
