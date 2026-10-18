// preview.html shows the loading pages with stand-ins for sign-in and the API,
// for looking at them on the dev server. The stack must never serve it: the
// build makes the two pages vite.config.ts names, and this is not one of them.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const FRONTEND = dirname(dirname(fileURLToPath(import.meta.url)));

describe("the preview page", () => {
  it("is not one of the pages the build makes", () => {
    const config = readFileSync(join(FRONTEND, "vite.config.ts"), "utf-8");
    const input = config.match(/input: \{([^}]*)\}/)?.[1] ?? "";

    expect(input).toContain('page("index.html")');
    expect(input).toContain('page("redirect.html")');
    expect(input).not.toContain("preview");
  });

  it("is imported by nothing the app's pages load", () => {
    for (const entry of ["src/main.tsx", "src/redirect.ts", "src/App.tsx"]) {
      expect(readFileSync(join(FRONTEND, entry), "utf-8")).not.toContain("preview");
    }
  });
});
