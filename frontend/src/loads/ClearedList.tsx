// The problems a corrected file no longer has, on request: a quiet button
// below the issues that are left, opening a short list (the cell, the rule,
// the value it had). It is reassurance, not work, so it stays closed until
// asked for, and it is asked of the server only then.
import { Anchor, Button, Group, Stack, Table, Text, Title } from "@mantine/core";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchCleared } from "../api/drafts";
import { useSession } from "../auth/Session";
import { Panel } from "../components/Panel";
import { useWideLayout } from "../layout/useWideLayout";
import classes from "../styles/app.module.css";
import { formatCount } from "./formatBytes";
import { issueKey, Value } from "./IssuesList";
import { cellLabel, ruleName } from "./texts";

const PAGE = 100;

export function ClearedList({ draftId, cleared }: { draftId: string; cleared: number }) {
  const { t } = useTranslation();
  const { api } = useSession();
  const wide = useWideLayout();
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);

  const list = useQuery({
    queryKey: ["draft-cleared", draftId, offset],
    queryFn: ({ signal }) => fetchCleared(api, draftId, offset, PAGE, signal),
    enabled: open,
    placeholderData: keepPreviousData,
  });

  if (cleared === 0) {
    return null;
  }

  const page = list.data;

  return (
    <Stack gap="sm">
      <Group>
        <Anchor
          component="button"
          type="button"
          size="sm"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          {open
            ? t("loads.comparison.hideCleared")
            : t("loads.comparison.showCleared", { count: cleared, n: formatCount(cleared) })}
        </Anchor>
      </Group>
      {open && (
        <Stack gap="sm" className={classes.rise}>
          <Title order={4} size="h5">
            {t("loads.comparison.clearedHeading")}
          </Title>
          {list.isError && !page && (
            <Panel tone="error">
              <Stack gap="sm" align="flex-start">
                <Text>{t("loads.comparison.clearedFailed")}</Text>
                <Button variant="default" size="compact-sm" onClick={() => void list.refetch()}>
                  {t("problem.retry")}
                </Button>
              </Stack>
            </Panel>
          )}
          {page && (
            <Stack gap="sm" className={list.isPlaceholderData ? classes.stale : undefined}>
              {wide ? (
                <Table className={classes.issueTable} verticalSpacing="xs">
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>{t("loads.issues.cell")}</Table.Th>
                      <Table.Th>{t("loads.issues.column")}</Table.Th>
                      <Table.Th>{t("loads.issues.rule")}</Table.Th>
                      <Table.Th>{t("loads.comparison.before")}</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {page.items.map((issue) => (
                      <Table.Tr key={issueKey(issue)}>
                        <Table.Td className={classes.cellName}>{cellLabel(t, issue)}</Table.Td>
                        <Table.Td>{issue.column_key ?? "—"}</Table.Td>
                        <Table.Td>{ruleName(t, issue.rule)}</Table.Td>
                        <Table.Td className={classes.valueCell}>
                          <Value value={issue.value} />
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              ) : (
                <Stack
                  gap={0}
                  component="ul"
                  aria-label={t("loads.comparison.clearedHeading")}
                  style={{ listStyle: "none", padding: 0, margin: 0 }}
                >
                  {page.items.map((issue) => (
                    <li key={issueKey(issue)} className={classes.issueCard}>
                      <Text size="sm">
                        <span className={classes.cellName}>{cellLabel(t, issue)}</span> ·{" "}
                        {ruleName(t, issue.rule)} · <Value value={issue.value} />
                      </Text>
                    </li>
                  ))}
                </Stack>
              )}
              {page.total > PAGE && (
                <Group justify="space-between" wrap="wrap" gap="sm">
                  <Text size="sm" c="dimmed" className={classes.number}>
                    {t("loads.issues.range", {
                      from: formatCount(page.offset + 1),
                      to: formatCount(page.offset + page.items.length),
                      total: formatCount(page.total),
                    })}
                  </Text>
                  <Group gap="xs">
                    <Button
                      variant="default"
                      size="compact-sm"
                      disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - PAGE))}
                    >
                      {t("loads.issues.previous")}
                    </Button>
                    <Button
                      variant="default"
                      size="compact-sm"
                      disabled={page.offset + page.items.length >= page.total}
                      onClick={() => setOffset(offset + PAGE)}
                    >
                      {t("loads.issues.next")}
                    </Button>
                  </Group>
                </Group>
              )}
            </Stack>
          )}
        </Stack>
      )}
    </Stack>
  );
}
