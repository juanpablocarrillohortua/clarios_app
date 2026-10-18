// The issues of a validated file, one per line: where (the cell, as Excel
// names it), which rule, the value found, and then why it probably happened
// and what to do in Excel, readable without opening anything. A second likely
// cause, when there is one, waits in a disclosure. The server keeps the list
// and pages it; the filters and the counts come from what it says it found.
//
// For a corrected file, an issue the earlier file did not have says "Nuevo",
// and a filter shows the new or the remaining ones.
//
// On a wide screen it is a table; on a phone, each issue is a short card.
// While the next page arrives the current one stays, dimmed: nothing blinks.
import {
  Badge,
  Button,
  Group,
  SegmentedControl,
  Select,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type Change,
  type DraftValidation,
  fetchIssues,
  type Issue,
  type IssueQuery,
  type Severity,
} from "../api/drafts";
import { useSession } from "../auth/Session";
import { Panel } from "../components/Panel";
import { useWideLayout } from "../layout/useWideLayout";
import classes from "../styles/app.module.css";
import { CopyIssues } from "./CopyIssues";
import { formatCount } from "./formatBytes";
import {
  cellLabel,
  firstReason,
  issueDetail,
  otherReasons,
  reasonFix,
  reasonWhy,
  ruleName,
} from "./texts";

const PAGE = 100;

// One issue per rule, per column, per row: together they name it.
export function issueKey(issue: Issue): string {
  return `${issue.row ?? ""}:${issue.column_key ?? ""}:${issue.rule}`;
}
type SeverityFilter = "all" | Severity;

export function SeverityMark({ issue }: { issue: Issue }) {
  const { t } = useTranslation();
  // The badge goes beneath the word when the column is narrow, never over the next one.
  return (
    <Group gap={6}>
      <span className={classes.dot} data-tone={issue.severity} aria-hidden="true" />
      <span>{t(`loads.severity.${issue.severity}`)}</span>
      {issue.change === "new" && (
        <Badge variant="outline" size="sm" tt="none" className={classes.newBadge}>
          {t("loads.issues.new")}
        </Badge>
      )}
    </Group>
  );
}

/** The cell as Excel names it, with the column's header beneath. */
export function CellName({ issue }: { issue: Issue }) {
  const { t } = useTranslation();
  return (
    <Stack gap={0}>
      <span className={classes.cellName}>{cellLabel(t, issue)}</span>
      {issue.column_key && (
        <Text size="xs" className={classes.secondary} style={{ overflowWrap: "anywhere" }}>
          {issue.column_key}
        </Text>
      )}
    </Stack>
  );
}

export function Value({ value }: { value: string | null }) {
  const { t } = useTranslation();
  if (value === null || value.trim() === "") {
    return <span className={classes.emptyValue}>{t("loads.issues.empty")}</span>;
  }
  return <span>«{value}»</span>;
}

/** Why it probably happened, then what to do in Excel; more causes on request. */
export function CauseAndFix({ issue }: { issue: Issue }) {
  const { t } = useTranslation();
  const detail = issueDetail(t, issue);
  const others = otherReasons(issue);
  const fix = reasonFix(t, issue);
  return (
    <Stack gap={4}>
      <Text size="sm" className={classes.secondary}>
        {reasonWhy(t, issue, firstReason(issue))}
      </Text>
      {fix && <Text size="sm">{fix}</Text>}
      {detail && (
        <span className={issue.rule === "cell.pattern" ? classes.code : classes.secondary}>
          {detail}
        </span>
      )}
      {others.length > 0 && (
        <details className={classes.disclosure}>
          <summary>{t("loads.issues.otherCauses", { count: others.length })}</summary>
          <Stack gap={6} mt={6}>
            {others.map((reason) => (
              <Stack key={reason.reason} gap={2}>
                <Text size="sm" className={classes.secondary}>
                  {reasonWhy(t, issue, reason)}
                </Text>
                <Text size="sm">{reasonFix(t, issue, reason)}</Text>
              </Stack>
            ))}
          </Stack>
        </details>
      )}
    </Stack>
  );
}

/** One issue as a card, for a phone. */
export function IssueCard({ issue }: { issue: Issue }) {
  const { t } = useTranslation();
  return (
    <li className={classes.issueCard}>
      <Stack gap={4}>
        <Group gap="xs" wrap="wrap" justify="space-between">
          <SeverityMark issue={issue} />
          <Text size="sm" className={classes.cellName}>
            {cellLabel(t, issue)}
          </Text>
        </Group>
        {issue.column_key && (
          <Text size="xs" className={classes.secondary} style={{ overflowWrap: "anywhere" }}>
            {issue.column_key}
          </Text>
        )}
        <Text size="sm" fw={600}>
          {ruleName(t, issue.rule)}
        </Text>
        <Text size="sm">
          <Value value={issue.value} />
        </Text>
        <CauseAndFix issue={issue} />
      </Stack>
    </li>
  );
}

/** The issues as a table, for a wide screen. */
export function IssueTable({ issues }: { issues: Issue[] }) {
  const { t } = useTranslation();
  return (
    <Table.ScrollContainer minWidth={760}>
      <Table className={classes.issueTable} verticalSpacing="sm" highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t("loads.issues.severity")}</Table.Th>
            <Table.Th>{t("loads.issues.cell")}</Table.Th>
            <Table.Th>{t("loads.issues.rule")}</Table.Th>
            <Table.Th>{t("loads.issues.value")}</Table.Th>
            <Table.Th>{t("loads.issues.causeAndFix")}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {issues.map((issue) => (
            <Table.Tr key={issueKey(issue)}>
              <Table.Td>
                <SeverityMark issue={issue} />
              </Table.Td>
              <Table.Td className={classes.cellColumn}>
                <CellName issue={issue} />
              </Table.Td>
              <Table.Td>{ruleName(t, issue.rule)}</Table.Td>
              <Table.Td className={classes.valueCell}>
                <Value value={issue.value} />
              </Table.Td>
              <Table.Td className={classes.causeCell}>
                <CauseAndFix issue={issue} />
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

interface IssuesListProps {
  draftId: string;
  validation: DraftValidation;
  /** Whether the draft is a correction compared issue by issue. */
  comparing?: boolean;
}

export function IssuesList({ draftId, validation, comparing = false }: IssuesListProps) {
  const { t } = useTranslation();
  const { api } = useSession();
  const wide = useWideLayout();
  const [severity, setSeverity] = useState<SeverityFilter>("all");
  const [rule, setRule] = useState<string | null>(null);
  const [column, setColumn] = useState<string | null>(null);
  const [change, setChange] = useState<Change | null>(null);
  const [offset, setOffset] = useState(0);

  const query: IssueQuery = {
    severity: severity === "all" ? undefined : severity,
    rule: rule ?? undefined,
    column: column ?? undefined,
    change: comparing ? (change ?? undefined) : undefined,
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
  const filtered = severity !== "all" || rule !== null || column !== null || change !== null;

  function clear() {
    setSeverity("all");
    setRule(null);
    setColumn(null);
    setChange(null);
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
      <Group justify="space-between" align="center" wrap="wrap" gap="sm">
        <Title order={3} size="h4">
          {t("loads.issues.heading")}
        </Title>
        <CopyIssues draftId={draftId} query={query} />
      </Group>

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
          {comparing && (
            <Select
              label={t("loads.issues.change")}
              placeholder={t("loads.issues.anyChange")}
              data={[
                { value: "new", label: t("loads.issues.changeNew") },
                { value: "remaining", label: t("loads.issues.changeRemaining") },
              ]}
              value={change}
              onChange={(value) => {
                setChange(value as Change | null);
                setOffset(0);
              }}
              clearable
              size="sm"
            />
          )}
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
            <IssueTable issues={page.items} />
          ) : (
            <Stack
              gap={0}
              component="ul"
              aria-label={t("loads.issues.heading")}
              style={{ listStyle: "none", padding: 0, margin: 0 }}
            >
              {page.items.map((issue) => (
                <IssueCard key={issueKey(issue)} issue={issue} />
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
