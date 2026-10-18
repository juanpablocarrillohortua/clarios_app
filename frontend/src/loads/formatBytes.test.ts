// Sizes read as Windows shows them, so the server's limit reads "300 MB".
import { describe, expect, it } from "vitest";

import { formatBytes, formatCount } from "./formatBytes";

describe("a size", () => {
  it.each([
    [0, "0 B"],
    [512, "512 B"],
    [1536, "1,5 KB"],
    [5 * 1024 * 1024, "5 MB"],
    [300 * 1024 * 1024, "300 MB"],
    [300 * 1024 * 1024 + 1, "300 MB"],
    [312.4 * 1024 * 1024, "312 MB"],
    [42.25 * 1024 * 1024, "42,3 MB"],
    [3 * 1024 ** 3, "3 GB"],
  ])("of %i bytes reads %s", (bytes, text) => {
    expect(formatBytes(bytes)).toBe(text);
  });
});

describe("a count", () => {
  it("groups thousands as Spanish does", () => {
    expect(formatCount(1234)).toBe("1234");
    expect(formatCount(12345)).toBe("12.345");
  });
});
