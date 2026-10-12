// Test helpers shared by the test files.
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";

import { App } from "../App";

/** Render the whole app as if the browser had opened `path`. */
export function renderAppAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

/** Make the health check answer as the API does, with the given version. */
export function stubHealthyApi(version = "0.0.0-test") {
  const fetchStub = vi.fn(
    async () =>
      new Response(
        JSON.stringify({ status: "ok", service: "clarios-energy", version, environment: "test" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
  );
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}
