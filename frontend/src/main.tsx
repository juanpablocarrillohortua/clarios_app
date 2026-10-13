// Browser entry point: mounts the app under the address-bar router, with
// Microsoft's sign-in.
import "@mantine/core/styles.css";
import "./i18n";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { App } from "./App";
import { createMsalAuthClient } from "./auth/msalAuthClient";

const root = document.getElementById("root");
if (!root) {
  throw new Error("index.html has no #root element");
}

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <App createAuthClient={createMsalAuthClient} />
    </BrowserRouter>
  </StrictMode>,
);
