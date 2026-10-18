// The dev server must keep the app's own pages and hand the stack's paths
// over, as Caddy does. Until 2026-10-13 it forwarded by prefix, so "/dash"
// took "/dashboard": opened or reloaded on the dev server, the dashboard was
// the stack's page with scripts the dev server does not have, and stayed
// blank.
import { describe, expect, it } from "vitest";

import { paths, REDIRECT_PAGE } from "./paths";
import { STACK_ROUTES } from "./stackRoutes";

function goesToTheStack(address: string): boolean {
  return STACK_ROUTES.some((route) => new RegExp(route).test(address));
}

describe("what the dev server hands to the stack", () => {
  it.each([
    "/",
    ...Object.values(paths),
    REDIRECT_PAGE,
    `${paths.dashboard}?tokenLife=60`,
    `${paths.dashboard}/`,
    "/src/main.tsx",
    "/no-such-section",
    // Caddy routes these three only with something after the slash.
    "/api",
    "/prefect",
    "/minio",
  ])("keeps %s for the app", (address) => {
    expect(goesToTheStack(address)).toBe(false);
  });

  it.each([
    "/api/config",
    "/api/me",
    "/api/docs",
    "/prefect/api/health",
    "/minio/",
    "/auth",
    "/auth/callback",
    "/auth?code=1",
    "/dash",
    "/dash/",
    "/dash/_dash-layout",
    "/dash?x=1",
  ])("hands %s over", (address) => {
    expect(goesToTheStack(address)).toBe(true);
  });
});
