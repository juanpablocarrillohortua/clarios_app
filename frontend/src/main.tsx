// Browser entry point: mounts the app under the address-bar router, with
// Microsoft's sign-in and Microsoft's Power BI embedding.
import "@mantine/core/styles.css";
// After Mantine's styles, so the app's own base wins where both set a value.
import "./styles/tokens.css";
import "./styles/global.css";
import "./i18n";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { App } from "./App";
import { createMsalAuthClient } from "./auth/msalAuthClient";
import type { LoadEmbedReport } from "./powerbi/reportEmbed";

const root = document.getElementById("root");
if (!root) {
  throw new Error("index.html has no #root element");
}

// Imported on demand, so the embedding library is a file of its own that the
// browser fetches when the dashboard opens, not with the app.
const loadEmbedReport: LoadEmbedReport = async () =>
  (await import("./powerbi/powerBiReportEmbed")).embedPowerBiReport;

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <App createAuthClient={createMsalAuthClient} loadEmbedReport={loadEmbedReport} />
    </BrowserRouter>
  </StrictMode>,
);
