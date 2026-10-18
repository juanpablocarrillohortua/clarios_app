// What a committed draft says, first and alone: it landed, how many rows,
// when, under which load, and where to read more. It arrives as a panel does,
// a short rise from where it is (opacity only under reduced motion).
//
// The dashboard does not show the rows yet: the figures are updated by a
// command today, and by a button of the app in a later increment. The panel
// says so rather than let the person look for them.
import { Anchor, Group, Stack, Text, Title } from "@mantine/core";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import type { Draft } from "../api/drafts";
import { Panel } from "../components/Panel";
import { loadPath, paths } from "../paths";
import classes from "../styles/app.module.css";
import { formatWhen } from "./dates";
import { formatCount } from "./formatBytes";

export function CommittedPanel({ draft }: { draft: Draft }) {
  const { t } = useTranslation();
  const rows = draft.validation?.rows_read ?? 0;
  const runId = draft.run_id ?? "";
  return (
    <Panel tone="success" className={classes.rise}>
      <Stack gap="xs" align="flex-start">
        <Title order={3} size="h4">
          {t("loads.committed.title")}
        </Title>
        <Text className={classes.secondary}>
          {t("loads.committed.rows", { count: rows, rows: formatCount(rows) })}
        </Text>
        {draft.committed_at && (
          <Text size="sm" c="dimmed">
            {t("loads.committed.when", { when: formatWhen(draft.committed_at) })}
          </Text>
        )}
        <Text size="sm" c="dimmed" className={classes.number}>
          {t("loads.committed.id", { id: runId })}
        </Text>
        <Text size="sm" c="dimmed">
          {t("loads.committed.figures")}
        </Text>
        <Group gap="lg">
          {runId && (
            <Anchor component={Link} to={loadPath(runId)} size="sm">
              {t("loads.committed.detail")}
            </Anchor>
          )}
          <Anchor component={Link} to={paths.history} size="sm">
            {t("loads.committed.history")}
          </Anchor>
        </Group>
      </Stack>
    </Panel>
  );
}
