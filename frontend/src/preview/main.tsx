// Entry point of preview.html, the dev server's look at the loading pages
// without signing in (src/preview/stage.ts says what is played). The real app
// is mounted, under an in-memory router opened at `?path=` (default /loads),
// with stand-ins for sign-in, the API and the upload. Never built for the
// stack: vite.config.ts does not name this page.
import "@mantine/core/styles.css";
import "../styles/tokens.css";
import "../styles/global.css";
import "../i18n";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";

import { App } from "../App";
import { PreviewRequest, previewAuth, previewFetch } from "./stage";

const root = document.getElementById("root");
if (!root) {
  throw new Error("preview.html has no #root element");
}

window.fetch = previewFetch as typeof window.fetch;
window.XMLHttpRequest = PreviewRequest as unknown as typeof XMLHttpRequest;
const path = new URLSearchParams(window.location.search).get("path") ?? "/loads";

createRoot(root).render(
  <StrictMode>
    <MemoryRouter initialEntries={[path]}>
      <App
        createAuthClient={async () => previewAuth}
        loadEmbedReport={() => Promise.reject(new Error("no report in the preview"))}
      />
    </MemoryRouter>
  </StrictMode>,
);
