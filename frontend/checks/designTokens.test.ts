// Every colour of the app is a design token of src/styles/tokens.css. This
// test reads every source file and the HTML pages, and fails for a colour
// value written anywhere else: a component names a token, never a colour.
//
// It reads the files from disk because the test runner hands CSS modules to
// tests as empty objects, even when asked for their text.
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

// The test browser replaces the global URL, so the path is taken apart as text.
const FRONTEND = dirname(dirname(fileURLToPath(import.meta.url)));
const TOKENS = join(FRONTEND, "src", "styles", "tokens.css");

function sourcesUnder(folder: string): string[] {
  return readdirSync(folder, { withFileTypes: true }).flatMap((entry) => {
    const path = join(folder, entry.name);
    if (entry.isDirectory()) {
      return sourcesUnder(path);
    }
    return /\.(ts|tsx|css)$/.test(entry.name) ? [path] : [];
  });
}

const files = [
  ...sourcesUnder(join(FRONTEND, "src")),
  join(FRONTEND, "index.html"),
  join(FRONTEND, "redirect.html"),
  join(FRONTEND, "preview.html"),
].filter((path) => path !== TOKENS);

const HEX = /#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})\b/gi;
const COLOUR_FUNCTION = /\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(/gi;

describe("the design tokens", () => {
  it("guards every source file, CSS modules included", () => {
    const names = files.map((path) => relative(FRONTEND, path).replaceAll("\\", "/"));
    expect(names.length).toBeGreaterThan(30);
    expect(names).toContain("src/styles/app.module.css");
    expect(names).toContain("index.html");
    expect(names).not.toContain("src/styles/tokens.css");
  });

  it.each(files.map((path) => [relative(FRONTEND, path), path]))(
    "%s writes no colour of its own",
    (_, path) => {
      const source = readFileSync(path, "utf-8");
      expect(source.match(HEX) ?? []).toEqual([]);
      expect(source.match(COLOUR_FUNCTION) ?? []).toEqual([]);
    },
  );

  it("finds a colour where one is written", () => {
    expect("color: #0e1330;".match(HEX)).toHaveLength(1);
    expect("background: rgba(0, 0, 0, 0.5);".match(COLOUR_FUNCTION)).toHaveLength(1);
  });
});
