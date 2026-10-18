// "Ver los cambios": the rows a catalogue's new version adds, changes and
// removes, on request. The browser's own disclosure: it opens at once, and the
// rows are asked of the server only then, one kind of change at a time, a page
// at a time. A changed row says which columns changed, before and after; an
// added or a removed row shows its first values.
//
// On a wide screen it is a table; on a phone, each row is a short card.
import { Button, Group, SegmentedControl, Stack, Table, Text } from "@mantine/core";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { type DifferenceRow, fetchDifference, type RowChange } from "../api/drafts";
import type { DifferenceCounts } from "../api/loads";
import { useSession } from "../auth/Session";
import { useWideLayout } from "../layout/useWideLayout";
import classes from "../styles/app.module.css";
import { formatCount } from "./formatBytes";

const PAGE = 50;
/** How many values of an added or removed row are shown beside its key. */
const VALUES_SHOWN = 4;

interface DifferenceListProps {
  draftId: string;
  counts: DifferenceCounts;
  /** The columns that identify a row, left out of its values. */
  naturalKey: string[];
}

function Quoted({ value }: { value: string | null }) {
  const { t } = useTranslation();
  return value === null || value === "" ? (
    <span className={classes.emptyValue}>{t("loads.difference.empty")}</span>
  ) : (
    <span>«{value}»</span>
  );
}

function What({ row, naturalKey }: { row: DifferenceRow; naturalKey: string[] }) {
  if (row.change === "changed") {
    return (
      <Stack gap={2}>
        {row.columns.map((column) => (
          <Text size="sm" key={column.column}>
            {column.column}: <Quoted value={column.before} /> → <Quoted value={column.after} />
          </Text>
        ))}
      </Stack>
    );
  }
  const shown = Object.entries(row.values)
    .filter(([column]) => !naturalKey.includes(column))
    .slice(0, VALUES_SHOWN);
  return (
    <Stack gap={2}>
      {shown.map(([column, value]) => (
        <Text size="sm" key={column} className={classes.secondary}>
          {column}: <Quoted value={value} />
        </Text>
      ))}
    </Stack>
  );
}

function keyOf(row: DifferenceRow): string {
  return row.key.map((part) => part ?? "").join(" · ");
}

export function DifferenceList({ draftId, counts, naturalKey }: DifferenceListProps) {
  const { t } = useTranslation();
  const { api } = useSession();
  const wide = useWideLayout();
  const [open, setOpen] = useState(false);
  const first: RowChange = counts.added > 0 ? "added" : counts.changed > 0 ? "changed" : "removed";
  const [change, setChange] = useState<RowChange>(first);
  const [offset, setOffset] = useState(0);

  const page = useQuery({
    queryKey: ["difference", draftId, change, offset],
    queryFn: ({ signal }) => fetchDifference(api, draftId, change, offset, PAGE, signal),
    enabled: open,
    placeholderData: keepPreviousData,
  });

  const total = { added: counts.added, changed: counts.changed, removed: counts.removed };
  const rows = page.data?.items ?? [];
  const shownTotal = page.data?.total ?? 0;
  const rowOf = (row: DifferenceRow) => row.row ?? row.previous_row;

  return (
    <details className={classes.disclosure} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{t("loads.difference.show")}</summary>
      {open && (
        <Stack gap="sm" mt="sm" className={classes.rise}>
          <SegmentedControl
            aria-label={t("loads.difference.label")}
            value={change}
            onChange={(value) => {
              setChange(value as RowChange);
              setOffset(0);
            }}
            data={(["added", "changed", "removed"] as const).map((kind) => ({
              value: kind,
              label: t(`loads.difference.${kind}`, { n: formatCount(total[kind]) }),
              disabled: total[kind] === 0,
            }))}
          />
          {page.isError && !page.data && (
            <Group gap="sm">
              <Text size="sm" role="alert">
                {t("loads.difference.failed")}
              </Text>
              <Button variant="default" size="compact-sm" onClick={() => void page.refetch()}>
                {t("problem.retry")}
              </Button>
            </Group>
          )}
          {page.isSuccess && rows.length === 0 && (
            <Text size="sm" c="dimmed">
              {t("loads.difference.none")}
            </Text>
          )}
          {rows.length > 0 &&
            (wide ? (
              <Table
                className={
                  page.isPlaceholderData
                    ? `${classes.issueTable} ${classes.stale}`
                    : classes.issueTable
                }
              >
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>{t("loads.difference.key")}</Table.Th>
                    <Table.Th>{t("loads.difference.row")}</Table.Th>
                    <Table.Th>
                      {change === "changed"
                        ? t("loads.difference.what")
                        : t("loads.difference.values")}
                    </Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {rows.map((row) => (
                    <Table.Tr key={`${keyOf(row)}:${rowOf(row)}`}>
                      <Table.Td className={classes.cellName}>{keyOf(row)}</Table.Td>
                      <Table.Td className={classes.number}>{rowOf(row)}</Table.Td>
                      <Table.Td>
                        <What row={row} naturalKey={naturalKey} />
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            ) : (
              <Stack
                component="ul"
                gap={0}
                p={0}
                m={0}
                aria-label={t("loads.difference.label")}
                className={page.isPlaceholderData ? classes.stale : undefined}
              >
                {rows.map((row) => (
                  <li key={`${keyOf(row)}:${rowOf(row)}`} className={classes.issueCard}>
                    <Group justify="space-between" wrap="nowrap">
                      <Text className={classes.cellName}>{keyOf(row)}</Text>
                      <Text size="xs" c="dimmed">
                        {t("loads.difference.row")} {rowOf(row)}
                      </Text>
                    </Group>
                    <What row={row} naturalKey={naturalKey} />
                  </li>
                ))}
              </Stack>
            ))}
          {shownTotal > PAGE && (
            <Group justify="space-between">
              <Text size="sm" c="dimmed">
                {t("loads.issues.range", {
                  from: formatCount(offset + 1),
                  to: formatCount(Math.min(offset + PAGE, shownTotal)),
                  total: formatCount(shownTotal),
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
                  disabled={offset + PAGE >= shownTotal}
                  onClick={() => setOffset(offset + PAGE)}
                >
                  {t("loads.issues.next")}
                </Button>
              </Group>
            </Group>
          )}
        </Stack>
      )}
    </details>
  );
}
