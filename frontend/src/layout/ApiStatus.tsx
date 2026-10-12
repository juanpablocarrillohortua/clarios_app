// One line saying whether the API answers and which version it runs. It is the
// app's proof that the path from the browser through Caddy to FastAPI works.
import { Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { fetchHealth } from "../api/health";

const RECHECK_EVERY_MS = 30_000;

export function ApiStatus() {
  const { t } = useTranslation();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => fetchHealth(signal),
    // The next check comes on the interval; retrying in between adds nothing.
    retry: false,
    refetchInterval: RECHECK_EVERY_MS,
  });

  if (health.isPending) {
    return (
      <Text size="sm" c="dimmed" role="status">
        {t("api.checking")}
      </Text>
    );
  }
  if (health.isError) {
    return (
      <Text size="sm" c="red.8" role="status">
        {t("api.offline")}
      </Text>
    );
  }
  return (
    <Text size="sm" c="dimmed" role="status">
      {t("api.online", { version: health.data.version })}
    </Text>
  );
}
