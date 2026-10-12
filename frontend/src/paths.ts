// The app's addresses. Caddy sends every path without a file extension to the
// app, except the ones the Caddyfile routes elsewhere: /api, /auth, /dash,
// /minio and /prefect. Do not add a section under one of those.
export const paths = {
  dashboard: "/dashboard",
  loads: "/loads",
} as const;
