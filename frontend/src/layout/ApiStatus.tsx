// One line saying whether the API answers and which version it runs. It is the
// app's proof that the path from the browser through Caddy to FastAPI works.
import { Group, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { fetchHealth } from "../api/health";
import classes from "../styles/app.module.css";

const RECHECK_EVERY_MS = 30_000;

type Tone = "checking" | "online" | "error";

/** A dot and the line. The words say the state; the colour only repeats it. */
function StatusLine({ tone, text }: { tone: Tone; text: string }) {
  return (
    <Group gap={8} wrap="nowrap">
      <span className={classes.dot} data-tone={tone} aria-hidden="true" />
      <Text size="sm" className={classes.status} data-tone={tone} role="status">
        {text}
      </Text>
    </Group>
  );
}

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
    return <StatusLine tone="checking" text={t("api.checking")} />;
  }
  if (health.isError) {
    return <StatusLine tone="error" text={t("api.offline")} />;
  }
  return <StatusLine tone="online" text={t("api.online", { version: health.data.version })} />;
}
