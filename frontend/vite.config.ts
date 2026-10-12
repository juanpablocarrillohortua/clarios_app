import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Where the compose stack listens. The dev server serves only the app and hands
// every other path to the stack, so the app calls /api exactly as it does when
// Caddy serves it. These are the paths the Caddyfile routes away from the app.
const STACK_URL = process.env.STACK_URL ?? "http://localhost:8080";
const STACK_PATHS = ["/api", "/auth", "/dash", "/minio", "/prefect"];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(
      STACK_PATHS.map((path) => [path, { target: STACK_URL, changeOrigin: true }]),
    ),
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    unstubGlobals: true,
  },
});
