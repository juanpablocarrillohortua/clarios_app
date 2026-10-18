// One line saying whether the API answers and which version it runs. It is the
// app's proof that the path from the browser through Caddy to FastAPI works.
import { Group, Text, Tooltip } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { fetchHealth } from "../api/health";
import classes from "../styles/app.module.css";

const RECHECK_EVERY_MS = 30_000;

type Tone = "checking" | "online" | "error";

/**
 * A dot and the line. The words say the state; the colour only repeats it.
 * Quiet, the words stay for screen readers and show on hover, and the dot
 * alone is in view.
 */
function StatusLine({ tone, text, quiet }: { tone: Tone; text: string; quiet: boolean }) {
  const line = (
    <Group gap={8} wrap="nowrap" p={quiet ? 4 : 0}>
      <span className={classes.dot} data-tone={tone} aria-hidden="true" />
      <Text
        size="sm"
        className={classes.status}
        data-tone={tone}
        data-quiet={quiet || undefined}
        role="status"
      >
        {text}
      </Text>
    </Group>
  );
  return quiet ? <Tooltip label={text}>{line}</Tooltip> : line;
}

interface ApiStatusProps {
  /** For the header: only a failure is said in words, everything else is the dot. */
  compact?: boolean;
}

export function ApiStatus({ compact = false }: ApiStatusProps) {
  const { t } = useTranslation();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => fetchHealth(signal),
    // The next check comes on the interval; retrying in between adds nothing.
    retry: false,
    refetchInterval: RECHECK_EVERY_MS,
  });

  if (health.isPending) {
    return <StatusLine tone="checking" text={t("api.checking")} quiet={compact} />;
  }
  if (health.isError) {
    return <StatusLine tone="error" text={t("api.offline")} quiet={false} />;
  }
  return (
    <StatusLine
      tone="online"
      text={t("api.online", { version: health.data.version })}
      quiet={compact}
    />
  );
}
