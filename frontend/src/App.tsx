// The app's providers and its route table. The router itself is supplied by
// the caller (the browser's in main.tsx, an in-memory one in tests).
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { Navigate, Route, Routes } from "react-router";

import { Shell } from "./layout/Shell";
import { DashboardPage } from "./pages/DashboardPage";
import { DataLoadingPage } from "./pages/DataLoadingPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { paths } from "./paths";
import { theme } from "./theme";

export function App() {
  // One client per mounted app, so two renders (as in tests) share no cache.
  const [queryClient] = useState(() => new QueryClient());

  return (
    <MantineProvider theme={theme}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<Navigate to={paths.dashboard} replace />} />
            <Route path={paths.dashboard} element={<DashboardPage />} />
            <Route path={paths.loads} element={<DataLoadingPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </QueryClientProvider>
    </MantineProvider>
  );
}
