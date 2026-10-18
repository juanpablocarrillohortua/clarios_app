// What a corrected file changed, said first in one sentence: how many problems
// were cleared, how many remain, how many are new. Then the three figures and
// the file it was compared with. When there is nothing to compare with (the
// earlier file is gone, was never validated, or was judged by other rules),
// one quiet line says why.
//
// The comparison is the server's (GET /api/drafts/{id}, "comparison"): the app
// only says it.
import { Group, List, Stack, Text, Title } from "@mantine/core";
import { useTranslation } from "react-i18next";

import type { Comparison } from "../api/drafts";
import { Panel } from "../components/Panel";
import { Figure } from "./DraftSummary";
import { formatCount } from "./formatBytes";
import { ruleName } from "./texts";

const WHEN = new Intl.DateTimeFormat("es", { dateStyle: "short", timeStyle: "short" });

function verdictOf(t: ReturnType<typeof useTranslation>["t"], comparison: Comparison): string {
  const { cleared, remaining, new: added } = comparison;
  if (remaining + added === 0 && cleared > 0) {
    return t("loads.comparison.allFixed", { count: cleared, n: formatCount(cleared) });
  }
  if (cleared === 0 && added === 0) {
    return t("loads.comparison.unchanged");
  }
  const parts = [
    cleared > 0
      ? t("loads.comparison.cleared", { count: cleared, n: formatCount(cleared) })
      : t("loads.comparison.noneCleared"),
  ];
  if (remaining > 0) {
    parts.push(t("loads.comparison.remaining", { count: remaining, n: formatCount(remaining) }));
  }
  if (added > 0) {
    parts.push(t("loads.comparison.new", { count: added, n: formatCount(added) }));
  }
  return parts.join(" · ");
}

export function ComparisonSummary({ comparison }: { comparison: Comparison }) {
  const { t } = useTranslation();

  if (comparison.state === "not_validated") {
    return null;
  }
  if (comparison.state !== "ready") {
    const why = {
      previous_gone: "loads.comparison.previousGone",
      previous_not_validated: "loads.comparison.previousNotValidated",
      rules_changed: "loads.comparison.rulesChanged",
    } as const;
    return (
      <Panel>
        <Text size="sm" c="dimmed" role="status">
          {t(why[comparison.state])}
        </Text>
      </Panel>
    );
  }

  const fixed = comparison.remaining + comparison.new === 0 && comparison.cleared > 0;
  const previous = comparison.previous;

  return (
    <Panel tone={fixed ? "success" : undefined}>
      <Stack gap="md">
        <Title order={3} size="h4" aria-live="polite">
          {verdictOf(t, comparison)}
        </Title>
        <Group gap="xl">
          <Figure
            label={t("loads.comparison.clearedLabel")}
            value={comparison.cleared}
            tone="success"
          />
          <Figure label={t("loads.comparison.remainingLabel")} value={comparison.remaining} />
          <Figure label={t("loads.comparison.newLabel")} value={comparison.new} tone="error" />
        </Group>
        {comparison.by_count_only.length > 0 && (
          <List size="sm" spacing={2} c="dimmed">
            {comparison.by_count_only.map((group) => (
              <List.Item key={`${group.rule}:${group.column ?? ""}`}>
                {t("loads.comparison.byCount", {
                  column: group.column ?? "",
                  rule: ruleName(t, group.rule),
                  before: formatCount(group.before),
                  after: formatCount(group.after),
                })}
              </List.Item>
            ))}
          </List>
        )}
        {previous && (
          <Text size="sm" c="dimmed" style={{ overflowWrap: "anywhere" }}>
            {t("loads.comparison.against", {
              name: previous.filename,
              when: previous.validated_at ? WHEN.format(new Date(previous.validated_at)) : "—",
            })}
            {comparison.previous_revalidated ? ` ${t("loads.comparison.revalidated")}` : ""}
          </Text>
        )}
      </Stack>
    </Panel>
  );
}
