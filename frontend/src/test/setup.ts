// Runs before every test file.
import "@testing-library/jest-dom/vitest";
import "../i18n";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  // The app notes in the tab's storage that it started a sign-in. A test must
  // not inherit that note from the one before.
  window.sessionStorage.clear();
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

// Mantine's select scrolls the highlighted choice into view; jsdom lays out
// nothing and has no such method.
Element.prototype.scrollIntoView = () => {};

// Mantine's growing textarea (the chat's box) sizes itself again when fonts
// finish loading; jsdom loads no fonts and has no document.fonts.
if (!("fonts" in document)) {
  Object.defineProperty(document, "fonts", {
    configurable: true,
    value: { addEventListener: () => {}, removeEventListener: () => {} },
  });
}
