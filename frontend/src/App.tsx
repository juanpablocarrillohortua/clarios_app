// The app's providers and its route table. The router itself is supplied by
// the caller (the browser's in main.tsx, an in-memory one in tests), and so are
// the sign-in client and what draws the Power BI report: this file never names
// Microsoft's libraries.
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { Navigate, Route, Routes } from "react-router";

import type { CreateAuthClient } from "./auth/authClient";
import { RequirePermission } from "./auth/RequirePermission";
import { Session, useSession } from "./auth/Session";
import { HistoryPage } from "./history/HistoryPage";
import { LoadDetailPage } from "./history/LoadDetailPage";
import { Shell } from "./layout/Shell";
import { DraftPage } from "./loads/DraftPage";
import { LoadsPage } from "./loads/LoadsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { paths } from "./paths";
import type { LoadEmbedReport } from "./powerbi/reportEmbed";
import { sections } from "./sections";
import { cssVariablesResolver, theme } from "./theme";

/** The root address opens the first section the user may open. */
function Home() {
  const { me } = useSession();
  const first = sections.find((section) => me.permissions.includes(section.permission));
  return <Navigate to={first?.path ?? paths.dashboard} replace />;
}

interface AppProps {
  /** Builds the sign-in client: MSAL's in the browser, a stand-in in tests. */
  createAuthClient: CreateAuthClient;
  /** Fetches what draws the Power BI report: Microsoft's library, or a stand-in. */
  loadEmbedReport: LoadEmbedReport;
}

export function App({ createAuthClient, loadEmbedReport }: AppProps) {
  // One client per mounted app, so two renders (as in tests) share no cache.
  const [queryClient] = useState(() => new QueryClient());

  return (
    // Dark only: the app follows the report it shows.
    <MantineProvider
      theme={theme}
      forceColorScheme="dark"
      cssVariablesResolver={cssVariablesResolver}
    >
      <QueryClientProvider client={queryClient}>
        {/* Nothing below is drawn until the user is signed in and may do something. */}
        <Session createAuthClient={createAuthClient}>
          <Routes>
            <Route element={<Shell />}>
              <Route index element={<Home />} />
              <Route
                path={paths.dashboard}
                element={
                  <RequirePermission permission="dashboard:view">
                    <DashboardPage loadEmbedReport={loadEmbedReport} />
                  </RequirePermission>
                }
              />
              <Route
                path={paths.loads}
                element={
                  <RequirePermission permission="loads:write">
                    <LoadsPage />
                  </RequirePermission>
                }
              />
              <Route
                path={paths.draft}
                element={
                  <RequirePermission permission="loads:write">
                    <DraftPage />
                  </RequirePermission>
                }
              />
              <Route
                path={paths.history}
                element={
                  <RequirePermission permission="loads:write">
                    <HistoryPage />
                  </RequirePermission>
                }
              />
              <Route
                path={paths.load}
                element={
                  <RequirePermission permission="loads:write">
                    <LoadDetailPage />
                  </RequirePermission>
                }
              />
              {/* Signed in again from the signed-out page: on to the app. */}
              <Route path={paths.signedOut} element={<Navigate to="/" replace />} />
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Routes>
        </Session>
      </QueryClientProvider>
    </MantineProvider>
  );
}
