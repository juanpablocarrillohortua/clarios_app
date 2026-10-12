// Runs before every test file.
import "@testing-library/jest-dom/vitest";
import "../i18n";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});

// jsdom has no layout, so it lacks two browser APIs Mantine's shell reads.
// They are plain stand-ins: nothing in a test depends on a screen size.
window.matchMedia = (query: string): MediaQueryList => ({
  matches: false,
  media: query,
  onchange: null,
  addListener: () => {},
  removeListener: () => {},
  addEventListener: () => {},
  removeEventListener: () => {},
  dispatchEvent: () => false,
});

window.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
