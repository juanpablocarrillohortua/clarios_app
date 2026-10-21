// The app's addresses. Caddy sends every path without a file extension to the
// app, except the ones the Caddyfile routes elsewhere: /api, /auth, /dash,
// /minio and /prefect. Do not add a section under one of those.
export const paths = {
  dashboard: "/dashboard",
  // The energy baseline models, and forecasts with them (ADR 0012).
  models: "/models",
  loads: "/loads",
  // One uploaded file, while it is checked. A draft's ID fills the segment.
  draft: "/loads/drafts/:draftId",
  // Every load that landed in the database, and one of them in detail.
  history: "/loads/history",
  load: "/loads/history/:runId",
  // Where Microsoft returns the browser after sign-out. It is registered as a
  // redirect URI of the app registration, so it cannot be renamed here alone.
  signedOut: "/signed-out",
} as const;

// The page Microsoft returns the browser to after sign-in (redirect.html). A
// file, not an app path. Also registered as a redirect URI.
export const REDIRECT_PAGE = "/redirect.html";

/** The address of one uploaded file. */
export function draftPath(draftId: string): string {
  return paths.draft.replace(":draftId", encodeURIComponent(draftId));
}

/** The address of one load in detail. */
export function loadPath(runId: string): string {
  return paths.load.replace(":runId", encodeURIComponent(runId));
}
