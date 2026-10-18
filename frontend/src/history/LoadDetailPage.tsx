// One load in detail (/loads/history/<id>): what landed, from which file, by
// whom and when, the rows of each sheet, and for a load from the app what its
// validation found and the warnings accepted, and for a catalogue how it
// differed from the version it replaced. The file it came from can be
// downloaded, through the API: the archive is never exposed.
//
// A load from the command line shows what it has: its sheets and rows, and a
// line that the app did not validate it.
import { Anchor, Box, Button, Group, Stack, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";

import { ApiError } from "../api/client";
import { downloadOriginal, fetchLoad, type LoadDetail, type RunCount } from "../api/loads";
import { useSession } from "../auth/Session";
import { Busy } from "../components/Busy";
import { Panel } from "../components/Panel";
import { saveFile } from "../components/saveFile";
import { Figure } from "../loads/DraftSummary";
import { formatDay, formatWhen } from "../loads/dates";
import { formatBytes, formatCount } from "../loads/formatBytes";
import { LoadsHeading, useDatasets } from "../loads/LoadsPage";
import { LoadsTabs } from "../loads/LoadsTabs";
import { removedRowsText, ruleName } from "../loads/texts";
import { draftPath, paths } from "../paths";
import classes from "../styles/app.module.css";
import { loaderName, loadName } from "./loadNames";

/** How much of a file's hash is shown; the whole is in the title, for copying. */
const HASH_SHOWN = 12;

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </>
  );
}

function Counts({ counts }: { counts: RunCount[] }) {
  const { t } = useTranslation();
  return (
    <Table className={classes.issueTable}>
      <Table.Tbody>
        {counts.map((count) => (
          <Table.Tr key={`${count.rule}:${count.column ?? ""}`}>
            <Table.Td>
              <Group gap={6} wrap="nowrap">
                <span className={classes.dot} data-tone={count.severity} aria-hidden="true" />
                <span>{t(`loads.severity.${count.severity}`)}</span>
              </Group>
            </Table.Td>
            <Table.Td>{ruleName(t, count.rule)}</Table.Td>
            <Table.Td>{count.column ?? ""}</Table.Td>
            <Table.Td ta="right" className={classes.number}>
              {formatCount(count.rows)}
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function Download({ load }: { load: LoadDetail }) {
  const { t } = useTranslation();
  const { api } = useSession();
  const [progress, setProgress] = useState<{ received: number; total: number } | null>(null);
  const [failed, setFailed] = useState(false);
  const original = load.original;
  if (!original) return null;

  async function download() {
    if (!original) return;
    setFailed(false);
    setProgress({ received: 0, total: original.size_bytes });
    try {
      const file = await downloadOriginal(api, load.run_id, (received, total) =>
        setProgress({ received, total: total || original.size_bytes }),
      );
      saveFile(file.blob, file.filename ?? original.filename);
    } catch {
      setFailed(true);
    } finally {
      setProgress(null);
    }
  }

  return (
    <Group gap="sm" align="center">
      <Button variant="default" onClick={() => void download()} disabled={progress !== null}>
        {t("load.download")}
      </Button>
      {progress && (
        <Text size="sm" c="dimmed" role="status" className={classes.number}>
          {t("load.downloading", {
            received: formatBytes(progress.received),
            total: formatBytes(progress.total),
          })}
        </Text>
      )}
      {failed && (
        <Text size="sm" role="alert" c="var(--ce-error)">
          {t("load.downloadFailed")}
        </Text>
      )}
    </Group>
  );
}

export function LoadDetailPage() {
  const { t } = useTranslation();
  const { api } = useSession();
  const { runId = "" } = useParams();
  const datasets = useDatasets();
  const load = useQuery({
    queryKey: ["loads", "detail", runId],
    queryFn: ({ signal }) => fetchLoad(api, runId, signal),
    retry: (failures, error) =>
      !(error instanceof ApiError && error.status === 404) && failures < 2,
  });

  let body: ReactNode;
  if (load.isPending) {
    body = <Busy text={t("load.loading")} />;
  } else if (load.isError) {
    const missing = load.error instanceof ApiError && load.error.status === 404;
    body = (
      <Panel tone={missing ? "warning" : "error"}>
        <Stack gap="sm" align="flex-start">
          <Text>{missing ? t("load.missing") : t("load.failed")}</Text>
          {!missing && (
            <Button variant="default" size="compact-sm" onClick={() => void load.refetch()}>
              {t("problem.retry")}
            </Button>
          )}
        </Stack>
      </Panel>
    );
  } else {
    const data = load.data;
    const difference = data.difference;
    const period =
      data.window_start && data.window_end
        ? t("load.periodValue", {
            from: formatDay(data.window_start),
            to: formatDay(data.window_end),
          })
        : t("load.none");
    body = (
      <Stack gap="lg">
        <Panel tone={data.state === "failed" ? "error" : undefined}>
          <Stack gap="md">
            <Group gap="xl">
              <Figure label={t("load.rowsWritten")} value={data.rows} />
              <Figure
                label={t("load.warningsAccepted")}
                value={data.warnings_accepted}
                tone="warning"
              />
            </Group>
            <dl className={classes.facts}>
              <Fact label={t("load.facts.state")}>{t(`history.states.${data.state}`)}</Fact>
              <Fact label={t("load.facts.who")}>{loaderName(t, data)}</Fact>
              <Fact label={t("load.facts.when")}>
                {formatWhen(data.finished_at ?? data.started_at)}
              </Fact>
              <Fact label={t("load.facts.period")}>{period}</Fact>
              <Fact label={t("load.facts.sheets")}>
                {data.sheets
                  .map((sheet) =>
                    t("load.sheetRows", {
                      count: sheet.rows ?? 0,
                      name: sheet.name,
                      rows: formatCount(sheet.rows ?? 0),
                    }),
                  )
                  .join(" · ")}
              </Fact>
              <Fact label={t("load.facts.origin")}>{t(`history.origins.${data.origin}`)}</Fact>
              {data.original && (
                <Fact label={t("load.facts.file")}>
                  {t("load.fileValue", {
                    name: data.original.filename,
                    size: formatBytes(data.original.size_bytes),
                  })}
                </Fact>
              )}
              {data.original && (
                <Fact label={t("load.facts.hash")}>
                  <span className={classes.code} title={data.original.content_hash}>
                    {data.original.content_hash.slice(0, HASH_SHOWN)}…
                  </span>
                </Fact>
              )}
              <Fact label={t("load.facts.id")}>
                <span className={classes.code}>{data.run_id}</span>
              </Fact>
            </dl>
            {data.error && (
              <Text size="sm" role="alert">
                {t("load.error", { error: data.error })}
              </Text>
            )}
            {data.origin === "cli" && (
              <Text size="sm" c="dimmed">
                {t("load.cliNote")}
              </Text>
            )}
            <Group gap="lg" align="center">
              <Download load={data} />
              {data.draft_available && data.draft_id && (
                <Anchor component={Link} to={draftPath(data.draft_id)} size="sm">
                  {t("load.draft")}
                </Anchor>
              )}
            </Group>
          </Stack>
        </Panel>

        {difference && (
          <Stack gap="xs">
            <Title order={3} size="h5">
              {t("load.differenceTitle")}
            </Title>
            <Group gap="xl">
              <Figure label={t("loads.confirm.added")} value={difference.added} />
              <Figure label={t("loads.confirm.changed")} value={difference.changed} />
              <Figure label={t("loads.confirm.removed")} value={difference.removed} />
              <Figure label={t("loads.confirm.unchanged")} value={difference.unchanged} />
            </Group>
            {difference.removed > 0 && difference.removed_rows && data.dataset_key && (
              <Text size="sm" className={classes.secondary}>
                {removedRowsText(t, data.dataset_key, difference.removed_rows)}
              </Text>
            )}
          </Stack>
        )}

        {data.validation?.accepted && data.validation.accepted.length > 0 && (
          <Stack gap="xs">
            <Title order={3} size="h5">
              {t("load.acceptedTitle")}
            </Title>
            <Counts counts={data.validation.accepted} />
          </Stack>
        )}

        {data.validation && (
          <Stack gap="xs">
            <Title order={3} size="h5">
              {t("load.validationTitle")}
            </Title>
            {data.validation.counts.length > 0 ? (
              <Counts counts={data.validation.counts} />
            ) : (
              <Text size="sm" c="dimmed">
                {t("load.validationNone")}
              </Text>
            )}
          </Stack>
        )}
      </Stack>
    );
  }

  return (
    <Box maw={960}>
      <Stack gap="lg">
        <Stack gap={4}>
          <LoadsHeading />
          <LoadsTabs />
          <Group justify="space-between" align="baseline" wrap="wrap" gap="xs" mt="xs">
            <Text fw={600}>{load.data ? loadName(t, datasets.data?.datasets, load.data) : ""}</Text>
            <Anchor component={Link} to={paths.history} size="sm">
              {t("load.back")}
            </Anchor>
          </Group>
          {load.data?.filename && (
            <Text size="sm" c="dimmed" style={{ overflowWrap: "anywhere" }}>
              {load.data.filename}
            </Text>
          )}
        </Stack>
        {body}
      </Stack>
    </Box>
  );
}
