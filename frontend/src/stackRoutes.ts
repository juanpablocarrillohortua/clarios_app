// The paths that belong to the compose stack and not to the app, written as
// the Caddyfile matches them: by whole path segment. The dev server forwards
// them to the stack (vite.config.ts), so the app calls /api there exactly as
// it does when Caddy serves it.
//
// Each entry is a regular expression, tested against the path and its query.
// A plain prefix would not do: "/dash" would also take "/dashboard", which is
// a page of the app, and the dev server would answer it with the stack's page.
export const STACK_ROUTES = [
  "^/api/",
  "^/prefect/",
  "^/minio/",
  // Caddy answers these two with and without something after them.
  "^/auth(?:[/?]|$)",
  "^/dash(?:[/?]|$)",
] as const;
