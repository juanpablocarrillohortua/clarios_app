// What a validation found, said first in one sentence: whether the file can be
// loaded. Then three figures, and when a rule broke in more rows than the list
// holds, how many rows that is.
import { Group, List, Stack, Text, Title } from "@mantine/core";
import { useTranslation } from "react-i18next";

import type { DraftValidation } from "../api/drafts";
import { Panel, type PanelTone } from "../components/Panel";
import classes from "../styles/app.module.css";
import { formatCount } from "./formatBytes";
import { ruleName } from "./texts";

export function Figure({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <Stack gap={0}>
      <Text className={classes.figure} data-tone={value > 0 ? tone : undefined}>
        {formatCount(value)}
      </Text>
      <Text className={classes.figureLabel}>{label}</Text>
    </Stack>
  );
}

export function DraftSummary({ validation }: { validation: DraftValidation }) {
  const { t } = useTranslation();
  const errors = validation.rows_with_errors;
  const warnings = validation.rows_with_warnings;

  let verdict: string;
  let tone: PanelTone;
  if (errors > 0) {
    verdict = t("loads.result.blocking", { count: errors, rows: formatCount(errors) });
    tone = "error";
  } else if (validation.blocking) {
    verdict = t("loads.result.fileBlocking");
    tone = "error";
  } else if (warnings > 0) {
    verdict = t("loads.result.warnings", { count: warnings, rows: formatCount(warnings) });
    tone = "warning";
  } else {
    verdict = t("loads.result.clean");
    tone = "success";
  }

  const capped = validation.counts.filter((count) => count.listed < count.rows);

  return (
    <Panel tone={tone}>
      <Stack gap="md">
        <Title order={3} size="h4">
          {verdict}
        </Title>
        <Group gap="xl">
          <Figure label={t("loads.result.rowsRead")} value={validation.rows_read} />
          <Figure label={t("loads.result.rowsWithErrors")} value={errors} tone="error" />
          <Figure label={t("loads.result.rowsWithWarnings")} value={warnings} tone="warning" />
        </Group>
        {capped.length > 0 && (
          <List size="sm" spacing={2} c="dimmed">
            {capped.map((count) => (
              <List.Item key={`${count.rule}:${count.column ?? ""}`}>
                {t("loads.issues.capped", {
                  column: count.column ?? "",
                  rule: ruleName(t, count.rule),
                  rows: formatCount(count.rows),
                  listed: formatCount(count.listed),
                })}
              </List.Item>
            ))}
          </List>
        )}
      </Stack>
    </Panel>
  );
}
