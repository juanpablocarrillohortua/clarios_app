// One uploaded file, at its own address (/loads/drafts/<id>): its validation,
// what was found, and the issues one by one. Arriving from an upload, the page
// validates at once. Arriving later (a reload, a return from sign-in) it
// shows what the server holds, and offers to validate if that never happened.
//
// The server decides what can be validated now: a file over its limit answers
// "not built yet" (501), and the page says that the file was kept.
//
// A file with problems is fixed in Excel and uploaded again from here, as a
// correction of this draft (increment 6, ADR 0009): by the one primary button,
// or by dropping the file anywhere on the page. The page then moves to the new
// draft, which says first what the correction cleared, kept and added.
//
// A draft without errors is confirmed from here too (increment 7): the
// confirmation says what would land, and "Confirmar carga" lands it. A
// committed draft then says so first, and keeps its issues to read.
import { Anchor, Box, Button, Group, Stack, Text, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type DragEvent, type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate, useParams } from "react-router";

import { ApiError } from "../api/client";
import { type Draft, fetchDraft, validateDraft } from "../api/drafts";
import { useSession } from "../auth/Session";
import { Busy } from "../components/Busy";
import { Panel } from "../components/Panel";
import { paths } from "../paths";
import classes from "../styles/app.module.css";
import { ClearedList } from "./ClearedList";
import { CommittedPanel } from "./CommittedPanel";
import { ComparisonSummary } from "./ComparisonSummary";
import { ConfirmPanel } from "./ConfirmPanel";
import { CorrectionUpload, useCorrection } from "./CorrectionUpload";
import { DraftSummary } from "./DraftSummary";
import { formatBytes } from "./formatBytes";
import { IssuesList } from "./IssuesList";
import { LoadsHeading, useDatasets } from "./LoadsPage";
import { LoadsTabs } from "./LoadsTabs";
import { named } from "./texts";

function isStatus(error: unknown, status: number): boolean {
  return error instanceof ApiError && error.status === status;
}

export function DraftPage() {
  const { t } = useTranslation();
  const { api } = useSession();
  const { draftId = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const datasets = useDatasets();

  const draft = useQuery({
    queryKey: ["draft", draftId],
    queryFn: ({ signal }) => fetchDraft(api, draftId, signal),
    retry: (failures, error) => !isStatus(error, 404) && failures < 2,
  });

  const validation = useMutation({
    mutationFn: () => validateDraft(api, draftId),
    onSuccess: (validated: Draft) => {
      queryClient.setQueryData(["draft", draftId], validated);
      void queryClient.invalidateQueries({ queryKey: ["draft-issues", draftId] });
    },
  });

  // Landed: the draft is committed now, and the history and each dataset's
  // last load have changed.
  const onCommitted = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["draft", draftId] });
    void queryClient.invalidateQueries({ queryKey: ["loads"] });
    void queryClient.invalidateQueries({ queryKey: ["datasets"] });
  }, [draftId, queryClient]);

  // Arriving from an upload: validate once. React's strict mode runs effects
  // twice, so a ref, not the mutation's state, keeps it to one request. It
  // remembers which draft: a correction moves this same page to a new one.
  const started = useRef<string | null>(null);
  const wanted = (location.state as { validate?: boolean } | null)?.validate === true;
  const status = draft.data?.status;
  useEffect(() => {
    if (wanted && status === "uploaded" && started.current !== draftId) {
      started.current = draftId;
      validation.mutate();
      // A reload must not validate again.
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [wanted, status, validation, navigate, location.pathname, draftId]);

  const data = draft.data;
  const dataset = datasets.data?.datasets.find((candidate) => candidate.key === data?.dataset_key);
  const limits = datasets.data?.limits;
  const correction = useCorrection(draftId, limits);

  // A corrected file dropped anywhere on the page goes up at once. Entering a
  // child fires enter and leave in pairs; counting them keeps the edge steady.
  const depth = useRef(0);
  const [dragging, setDragging] = useState(false);
  const hasIssues = (draft.data?.validation?.counts.length ?? 0) > 0;
  const droppable =
    draft.data?.status === "validated" && hasIssues && Boolean(limits) && !correction.busy;
  const drop = {
    onDragEnter(event: DragEvent) {
      if (!droppable) return;
      event.preventDefault();
      depth.current += 1;
      setDragging(true);
    },
    onDragOver(event: DragEvent) {
      if (droppable) event.preventDefault();
    },
    onDragLeave() {
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setDragging(false);
    },
    onDrop(event: DragEvent) {
      event.preventDefault();
      depth.current = 0;
      setDragging(false);
      if (droppable) correction.choose(event.dataTransfer.files[0]);
    },
  };
  const another = `${paths.loads}?dataset=${encodeURIComponent(data?.dataset_key ?? "")}`;
  const anotherLink = (
    <Anchor component={Link} to={another} size="sm">
      {t("loads.draft.another")}
    </Anchor>
  );

  let body: ReactNode;
  if (draft.isPending) {
    body = <Busy text={t("loads.draft.loading")} />;
  } else if (draft.isError) {
    body = (
      <Panel tone={isStatus(draft.error, 404) ? "warning" : "error"}>
        <Stack gap="sm" align="flex-start">
          <Text>
            {isStatus(draft.error, 404) ? t("loads.draft.missing") : t("loads.draft.failed")}
          </Text>
          {isStatus(draft.error, 404) ? (
            anotherLink
          ) : (
            <Button variant="default" size="compact-sm" onClick={() => void draft.refetch()}>
              {t("problem.retry")}
            </Button>
          )}
        </Stack>
      </Panel>
    );
  } else if (data && (data.status === "failed" || data.status === "receiving")) {
    body = (
      <Panel tone="warning">
        <Stack gap="sm" align="flex-start">
          <Text>{t("loads.draft.unfinished")}</Text>
          {anotherLink}
        </Stack>
      </Panel>
    );
  } else if (validation.isPending) {
    body = <Busy text={t("loads.draft.validating")} />;
  } else if (
    (validation.isError && isStatus(validation.error, 501)) ||
    (data?.status === "uploaded" &&
      limits &&
      (data.size_bytes ?? 0) > limits.validated_in_request_bytes)
  ) {
    body = (
      <Panel tone="info">
        <Stack gap="xs" align="flex-start">
          <Title order={3} size="h4">
            {t("loads.draft.archivedTitle")}
          </Title>
          <Text className={classes.secondary}>
            {t("loads.draft.notValidated", {
              limit: formatBytes(limits?.validated_in_request_bytes ?? 0),
            })}
          </Text>
        </Stack>
      </Panel>
    );
  } else if (data?.status === "committed" && data.validation) {
    body = (
      <Stack gap="lg">
        <CommittedPanel draft={data} />
        <DraftSummary validation={data.validation} />
        <IssuesList draftId={data.draft_id} validation={data.validation} comparing={false} />
      </Stack>
    );
  } else if (data?.status === "validated" && data.validation) {
    const comparison = data.comparison;
    const comparing = comparison?.state === "ready";
    const confirmable = !data.validation.blocking;
    body = (
      <Stack gap="lg">
        {comparison && <ComparisonSummary comparison={comparison} />}
        <DraftSummary validation={data.validation} />
        {confirmable && <ConfirmPanel draft={data} dataset={dataset} onCommitted={onCommitted} />}
        {hasIssues && limits && (
          <CorrectionUpload
            correction={correction}
            limits={limits}
            dragging={dragging}
            primary={!confirmable}
          />
        )}
        <IssuesList draftId={data.draft_id} validation={data.validation} comparing={comparing} />
        {comparing && comparison && (
          <ClearedList draftId={data.draft_id} cleared={comparison.cleared} />
        )}
      </Stack>
    );
  } else {
    body = (
      <Panel tone={validation.isError ? "error" : undefined}>
        <Stack gap="sm" align="flex-start">
          {validation.isError && (
            <Text role="alert">
              {isStatus(validation.error, 409)
                ? t("loads.draft.busy")
                : t("loads.draft.validateFailed")}
            </Text>
          )}
          <Button onClick={() => validation.mutate()}>{t("loads.draft.validate")}</Button>
        </Stack>
      </Panel>
    );
  }

  return (
    <Box maw={960} className={classes.pageDrop} data-dragging={dragging || undefined} {...drop}>
      <Stack gap="lg">
        <Stack gap={4}>
          <LoadsHeading />
          <LoadsTabs />
          <Group justify="space-between" align="baseline" wrap="wrap" gap="xs" mt="xs">
            <Text fw={600}>
              {dataset ? named(t, dataset.label, dataset.key) : (data?.dataset_key ?? "")}
            </Text>
            {data && anotherLink}
          </Group>
          {data && (
            <Text size="sm" c="dimmed" style={{ overflowWrap: "anywhere" }}>
              {t("loads.draft.file", {
                name: data.filename,
                size: formatBytes(data.size_bytes ?? data.declared_bytes),
              })}
            </Text>
          )}
        </Stack>
        {body}
      </Stack>
    </Box>
  );
}
