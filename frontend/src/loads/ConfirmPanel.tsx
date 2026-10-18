// "Confirmar carga": what landing a validated draft would write, and the one
// action that writes it (increment 7). The server says what would be written
// (GET /api/drafts/{id}/confirmation); the page says it, in this order:
//
// 1. one sentence: how many rows, into which dataset;
// 2. for a catalogue, how the new version differs from the current one, as
//    figures, with the rows themselves on request, and what Silver does with
//    a row the new version leaves out; for a movement, the period its rows
//    cover and the loads that already cover part of it;
// 3. the warnings it carries, and a tick that accepts them;
// 4. the action. Pressed once, it is disabled at once and says so; past a
//    short moment a bar sweeps under it. There is no "Cancelar": the commit is
//    one short transaction the server cannot stop half way, and repeating it
//    is safe, which the page says rather than offering a cancel that would lie.
//
// Pink is this button only. The upload of a corrected file stays possible,
// as a quiet button, beside it.
import { Anchor, Button, Checkbox, Group, List, Stack, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import type { Dataset } from "../api/datasets";
import { type Confirmation, type Draft, fetchConfirmation } from "../api/drafts";
import type { LoadDetail } from "../api/loads";
import { useSession } from "../auth/Session";
import { Busy } from "../components/Busy";
import { Panel } from "../components/Panel";
import { loadPath } from "../paths";
import classes from "../styles/app.module.css";
import { DifferenceList } from "./DifferenceList";
import { Figure } from "./DraftSummary";
import { formatDay, formatWhen } from "./dates";
import { formatCount } from "./formatBytes";
import { named, removedRowsText, ruleName } from "./texts";
import { type CommitFailure, useCommit } from "./useCommit";

/** How long a commit runs before the bar appears: a fast one shows no bar at all. */
const SWEEP_AFTER_MS = 300;

/** Refusals the person can answer by pressing again, and what the button says. */
const AGAIN: Partial<Record<CommitFailure, "check" | "retry">> = {
  unknown: "check",
  draftBusy: "check",
  datasetBusy: "retry",
  failed: "retry",
};

function useDelayed(on: boolean, delay: number): boolean {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (!on) {
      setShown(false);
      return;
    }
    const timer = window.setTimeout(() => setShown(true), delay);
    return () => window.clearTimeout(timer);
  }, [on, delay]);
  return shown;
}

interface ConfirmPanelProps {
  draft: Draft;
  dataset: Dataset | undefined;
  onCommitted: (load: LoadDetail) => void;
}

export function ConfirmPanel({ draft, dataset, onCommitted }: ConfirmPanelProps) {
  const { t } = useTranslation();
  const { api } = useSession();
  // An acceptance is of the warnings shown: it names their validation's digest,
  // so a validation that changed leaves the box unticked.
  const [acceptedFor, setAcceptedFor] = useState<string | null>(null);
  const confirmation = useQuery({
    // A validation again is a new confirmation: what it says may have changed.
    queryKey: ["confirmation", draft.draft_id, draft.validation?.validated_at ?? null],
    queryFn: ({ signal }) => fetchConfirmation(api, draft.draft_id, signal),
  });
  const { state, commit, committing } = useCommit(draft.draft_id, onCommitted);
  const sweeping = useDelayed(committing, SWEEP_AFTER_MS);

  if (confirmation.isPending) {
    return <Busy text={t("loads.confirm.loading")} />;
  }
  if (confirmation.isError) {
    return (
      <Panel tone="error">
        <Stack gap="sm" align="flex-start">
          <Text>{t("loads.confirm.failed")}</Text>
          <Button variant="default" size="compact-sm" onClick={() => void confirmation.refetch()}>
            {t("problem.retry")}
          </Button>
        </Stack>
      </Panel>
    );
  }

  const shown: Confirmation = confirmation.data;
  if (shown.blocked_by === "already_loaded" && shown.already_loaded_run_id) {
    return (
      <Panel tone="info">
        <Stack gap="xs" align="flex-start">
          <Text>{t("loads.confirm.alreadyLoaded")}</Text>
          <Anchor component={Link} to={loadPath(shown.already_loaded_run_id)} size="sm">
            {t("loads.confirm.seeLoad")}
          </Anchor>
        </Stack>
      </Panel>
    );
  }
  if (!shown.can_confirm || !shown.validation_digest) {
    // Errors, or nothing to land: the summary above says why.
    return null;
  }

  const datasetName = dataset ? named(t, dataset.label, dataset.key) : shown.dataset_key;
  const digest = shown.validation_digest;
  const accepted = acceptedFor === digest;
  const warningRows = shown.warnings.reduce((sum, warning) => sum + warning.rows, 0);
  const mustAccept = shown.warnings_to_accept && !accepted;
  const difference = shown.difference;
  const changes = difference ? difference.added + difference.changed + difference.removed : 0;
  const failure = state.phase === "failed" ? state.failure : null;
  const again = failure ? AGAIN[failure] : undefined;

  function confirm() {
    void commit({
      validation_digest: digest,
      accept_warnings: shown.warnings_to_accept && accepted,
      base_run_id: shown.current_version?.run_id ?? null,
    });
  }

  return (
    <Panel>
      <Stack gap="md">
        <Title order={3} size="h4">
          {t("loads.confirm.ready", {
            count: shown.rows,
            rows: formatCount(shown.rows),
            dataset: datasetName,
          })}
        </Title>

        {difference && (
          <Stack gap="xs">
            <Group gap="xl">
              <Figure label={t("loads.confirm.added")} value={difference.added} />
              <Figure label={t("loads.confirm.changed")} value={difference.changed} />
              <Figure label={t("loads.confirm.removed")} value={difference.removed} />
              <Figure label={t("loads.confirm.unchanged")} value={difference.unchanged} />
            </Group>
            <Text size="sm" c="dimmed">
              {shown.current_version
                ? t("loads.confirm.current", {
                    file: shown.current_version.filename ?? "",
                    when: shown.current_version.finished_at
                      ? formatWhen(shown.current_version.finished_at)
                      : "",
                  })
                : t("loads.confirm.firstVersion")}
            </Text>
            {difference.removed > 0 && difference.removed_rows && (
              <Text size="sm" className={classes.secondary}>
                {removedRowsText(t, shown.dataset_key, difference.removed_rows)}
              </Text>
            )}
            {changes > 0 && (
              <DifferenceList
                draftId={draft.draft_id}
                counts={difference}
                naturalKey={dataset?.natural_key ?? []}
              />
            )}
          </Stack>
        )}

        {shown.period && (
          <Text size="sm" className={classes.secondary}>
            {t("loads.confirm.period", {
              from: formatDay(shown.period.start),
              to: formatDay(shown.period.end),
            })}
          </Text>
        )}
        {shown.overlapping_loads.length > 0 && (
          <Stack gap={4}>
            <Text size="sm" c="dimmed">
              {t("loads.confirm.overlapping", { count: shown.overlapping_loads.length })}
            </Text>
            <List size="sm" spacing={2} c="dimmed">
              {shown.overlapping_loads.map((load) => (
                <List.Item key={load.run_id}>
                  <Anchor component={Link} to={loadPath(load.run_id)} size="sm">
                    {t("loads.confirm.overlappingLoad", {
                      file: load.filename ?? "",
                      from: load.window_start ? formatDay(load.window_start) : "",
                      to: load.window_end ? formatDay(load.window_end) : "",
                      when: load.finished_at ? formatWhen(load.finished_at) : "",
                    })}
                  </Anchor>
                </List.Item>
              ))}
            </List>
            <Text size="sm" c="dimmed">
              {t("loads.confirm.overlappingRule")}
            </Text>
          </Stack>
        )}

        {shown.warnings_to_accept && (
          <Stack gap="xs">
            <Text fw={600} size="sm">
              {t("loads.confirm.warningsTitle")}
            </Text>
            <List size="sm" spacing={2}>
              {shown.warnings.map((warning) => (
                <List.Item key={`${warning.rule}:${warning.column ?? ""}`}>
                  {warning.column
                    ? t("loads.confirm.warningLine", {
                        count: warning.rows,
                        rule: ruleName(t, warning.rule),
                        column: warning.column,
                        rows: formatCount(warning.rows),
                      })
                    : t("loads.confirm.warningFile", {
                        count: warning.rows,
                        rule: ruleName(t, warning.rule),
                        rows: formatCount(warning.rows),
                      })}
                </List.Item>
              ))}
            </List>
            <Checkbox
              checked={accepted}
              disabled={committing}
              onChange={(event) => setAcceptedFor(event.currentTarget.checked ? digest : null)}
              label={t("loads.confirm.accept", {
                count: warningRows,
                n: formatCount(warningRows),
              })}
            />
          </Stack>
        )}

        <Stack gap="xs" align="flex-start">
          <Group gap="sm" align="center">
            <Button onClick={confirm} disabled={mustAccept || committing}>
              {committing ? t("loads.confirm.confirming") : t("loads.confirm.confirm")}
            </Button>
            {mustAccept && (
              <Text size="sm" c="dimmed">
                {t("loads.confirm.acceptFirst")}
              </Text>
            )}
          </Group>
          {committing && (
            <Stack gap={6} w="100%" maw={420} role="status">
              {sweeping && (
                <div className={classes.progressTrack} aria-hidden="true">
                  <div className={classes.progressSweep} />
                </div>
              )}
              <Text size="sm" className={classes.secondary}>
                {t("loads.confirm.writing", { count: shown.rows, rows: formatCount(shown.rows) })}
              </Text>
              <Text size="xs" c="dimmed">
                {t("loads.confirm.noCancel")}
              </Text>
            </Stack>
          )}
          {failure && (
            <Group gap="sm" align="center">
              <Text size="sm" role="alert" c="var(--ce-error)">
                {t(`loads.confirm.errors.${failure === "alreadyLoaded" ? "failed" : failure}`)}
              </Text>
              {again && (
                <Button variant="default" size="compact-sm" onClick={confirm}>
                  {t(`loads.confirm.${again}`)}
                </Button>
              )}
            </Group>
          )}
        </Stack>
      </Stack>
    </Panel>
  );
}
