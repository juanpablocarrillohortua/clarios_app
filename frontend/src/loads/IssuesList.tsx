// The issues of a validated file, one per line: where (row and column), which
// rule, the value found and what is wrong. The server keeps the list and
// pages it; the filters and the counts come from what it says it found.
//
// On a wide screen it is a table; on a phone, each issue is a short card.
// While the next page arrives the current one stays, dimmed: nothing blinks.
import { Button, Group, SegmentedControl, Select, Stack, Table, Text, Title } from "@mantine/core";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { type DraftValidation, fetchIssues, type Issue, type Severity } from "../api/drafts";
import { useSession } from "../auth/Session";
import { Panel } from "../components/Panel";
import { useWideLayout } from "../layout/useWideLayout";
import classes from "../styles/app.module.css";
import { formatCount } from "./formatBytes";
import { issueDetail, issueMessage, ruleName } from "./texts";

const PAGE = 100;

// One issue per rule, per column, per row: together they name it.
function issueKey(issue: Issue): string {
  return `${issue.row ?? ""}:${issue.column_key ?? ""}:${issue.rule}`;
}
type SeverityFilter = "all" | Severity;

function SeverityMark({ severity }: { severity: Severity }) {
  const { t } = useTranslation();
  return (
    <Group gap={6} wrap="nowrap">
      <span className={classes.dot} data-tone={severity} aria-hidden="true" />
      <span>{t(`loads.severity.${severity}`)}</span>
    </Group>
  );
}

function Where({ issue }: { issue: Issue }) {
  const { t } = useTranslation();
  if (issue.row === null) {
    return <span>{t("loads.issues.wholeFile")}</span>;
  }
  return <span className={classes.number}>{formatCount(issue.row)}</span>;
}

function ColumnName({ issue }: { issue: Issue }) {
  if (issue.column && issue.column_key) {
    return (
      <span>
        <span className={classes.number}>{issue.column}</span> · {issue.column_key}
      </span>
    );
  }
  return <span>{issue.column ?? issue.column_key ?? "—"}</span>;
}

function Value({ value }: { value: string | null }) {
  const { t } = useTranslation();
  if (value === null || value.trim() === "") {
    return <span className={classes.emptyValue}>{t("loads.issues.empty")}</span>;
  }
  return <span>«{value}»</span>;
}

function Detail({ issue }: { issue: Issue }) {
  const { t } = useTranslation();
  const detail = issueDetail(t, issue);
  return (
    <Stack gap={2}>
      <span>{issueMessage(t, issue)}</span>
      {detail && (
        <span className={issue.rule === "cell.pattern" ? classes.code : classes.secondary}>
          {detail}
        </span>
      )}
    </Stack>
  );
}

interface IssuesListProps {
  draftId: string;
  validation: DraftValidation;
}

export function IssuesList({ draftId, validation }: IssuesListProps) {
  const { t } = useTranslation();
  const { api } = useSession();
  const wide = useWideLayout();
  const [severity, setSeverity] = useState<SeverityFilter>("all");
  const [rule, setRule] = useState<string | null>(null);
  const [column, setColumn] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);

  const query = {
    severity: severity === "all" ? undefined : severity,
    rule: rule ?? undefined,
    column: column ?? undefined,
    offset,
    limit: PAGE,
  };
  const issues = useQuery({
    queryKey: ["draft-issues", draftId, validation.validated_at, query],
    queryFn: ({ signal }) => fetchIssues(api, draftId, query, signal),
    placeholderData: keepPreviousData,
  });

  const listed = (wanted: Severity) =>
    validation.counts
      .filter((count) => count.severity === wanted)
      .reduce((sum, count) => sum + count.listed, 0);
  const all = validation.counts.reduce((sum, count) => sum + count.listed, 0);
  const rules = [...new Set(validation.counts.map((count) => count.rule))];
  const columns = [
    ...new Set(validation.counts.flatMap((count) => (count.column ? [count.column] : []))),
  ];
  const filtered = severity !== "all" || rule !== null || column !== null;

  function clear() {
    setSeverity("all");
    setRule(null);
    setColumn(null);
    setOffset(0);
  }

  if (all === 0) {
    return (
      <Panel tone="success">
        <Text>{t("loads.issues.none")}</Text>
      </Panel>
    );
  }

  const page = issues.data;
  const severities = (["error", "warning", "info"] as const).filter((each) => listed(each) > 0);

  return (
    <Stack gap="md">
      <Title order={3} size="h4">
        {t("loads.issues.heading")}
      </Title>

      <Stack gap="sm">
        <SegmentedControl
          aria-label={t("loads.issues.severityFilter")}
          value={severity}
          onChange={(value) => {
            setSeverity(value as SeverityFilter);
            setOffset(0);
          }}
          data={[
            { value: "all", label: t("loads.issues.allCount", { n: formatCount(all) }) },
            ...severities.map((each) => ({
              value: each,
              label: t(`loads.issues.${each}Count`, { n: formatCount(listed(each)) }),
            })),
          ]}
          fullWidth={!wide}
          orientation={wide ? "horizontal" : "vertical"}
        />
        <Group grow={!wide} gap="sm" wrap="wrap" align="flex-end">
          <Select
            label={t("loads.issues.rule")}
            placeholder={t("loads.issues.anyRule")}
            data={rules.map((each) => ({ value: each, label: ruleName(t, each) }))}
            value={rule}
            onChange={(value) => {
              setRule(value);
              setOffset(0);
            }}
            clearable
            size="sm"
          />
          <Select
            label={t("loads.issues.column")}
            placeholder={t("loads.issues.anyColumn")}
            data={columns}
            value={column}
            onChange={(value) => {
              setColumn(value);
              setOffset(0);
            }}
            clearable
            searchable
            size="sm"
          />
        </Group>
      </Stack>

      {issues.isError && !page && (
        <Panel tone="error">
          <Stack gap="sm" align="flex-start">
            <Text>{t("loads.issues.failed")}</Text>
            <Button variant="default" size="compact-sm" onClick={() => void issues.refetch()}>
              {t("problem.retry")}
            </Button>
          </Stack>
        </Panel>
      )}

      {page && page.total === 0 && (
        <Panel>
          <Stack gap="sm" align="flex-start">
            <Text>{t("loads.issues.noMatch")}</Text>
            {filtered && (
              <Button variant="default" size="compact-sm" onClick={clear}>
                {t("loads.issues.clearFilters")}
              </Button>
            )}
          </Stack>
        </Panel>
      )}

      {page && page.total > 0 && (
        <Stack gap="sm" className={issues.isPlaceholderData ? classes.stale : undefined}>
          {wide ? (
            <Table.ScrollContainer minWidth={720}>
              <Table className={classes.issueTable} verticalSpacing="xs" highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>{t("loads.issues.severity")}</Table.Th>
                    <Table.Th>{t("loads.issues.row")}</Table.Th>
                    <Table.Th>{t("loads.issues.column")}</Table.Th>
                    <Table.Th>{t("loads.issues.rule")}</Table.Th>
                    <Table.Th>{t("loads.issues.value")}</Table.Th>
                    <Table.Th>{t("loads.issues.detail")}</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {page.items.map((issue) => (
                    <Table.Tr key={issueKey(issue)}>
                      <Table.Td>
                        <SeverityMark severity={issue.severity} />
                      </Table.Td>
                      <Table.Td>
                        <Where issue={issue} />
                      </Table.Td>
                      <Table.Td>
                        <ColumnName issue={issue} />
                      </Table.Td>
                      <Table.Td>{ruleName(t, issue.rule)}</Table.Td>
                      <Table.Td className={classes.valueCell}>
                        <Value value={issue.value} />
                      </Table.Td>
                      <Table.Td>
                        <Detail issue={issue} />
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          ) : (
            <Stack
              gap={0}
              component="ul"
              aria-label={t("loads.issues.heading")}
              style={{ listStyle: "none", padding: 0, margin: 0 }}
            >
              {page.items.map((issue) => (
                <li key={issueKey(issue)} className={classes.issueCard}>
                  <Stack gap={4}>
                    <Group gap="xs" wrap="wrap">
                      <SeverityMark severity={issue.severity} />
                      <Text size="sm" c="dimmed">
                        {t("loads.issues.row")} <Where issue={issue} /> ·{" "}
                        <ColumnName issue={issue} />
                      </Text>
                    </Group>
                    <Text size="sm" fw={600}>
                      {ruleName(t, issue.rule)}
                    </Text>
                    <Text size="sm">
                      <Value value={issue.value} />
                    </Text>
                    <Text size="sm" component="div">
                      <Detail issue={issue} />
                    </Text>
                  </Stack>
                </li>
              ))}
            </Stack>
          )}

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
        </Stack>
      )}
    </Stack>
  );
}
