// The data-loading section: choose a dataset, choose a file, upload it
// (increment 5 of docs/frontend-plan.md). Once the server has stored the file
// the page moves to the draft's own address, where it is validated and its
// issues are listed; that address survives a reload or a new sign-in.
//
// One primary action at a time: "Subir y validar" appears once a dataset and a
// file are chosen, and becomes the progress of the upload while it runs.
import { Box, Button, Group, Skeleton, Stack, Text, Title, VisuallyHidden } from "@mantine/core";
import { useReducedMotion } from "@mantine/hooks";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router";

import { type Dataset, fetchDatasets } from "../api/datasets";
import type { Draft } from "../api/drafts";
import { useSession } from "../auth/Session";
import { Panel } from "../components/Panel";
import { useWideLayout } from "../layout/useWideLayout";
import { draftPath } from "../paths";
import classes from "../styles/app.module.css";
import { DatasetPicker } from "./DatasetPicker";
import { FilePicker } from "./FilePicker";
import { formatBytes } from "./formatBytes";
import { UploadProgress } from "./UploadProgress";
import { type UploadStop, useUpload } from "./useUpload";

/** How long the list of datasets is trusted before it is asked for again. */
const DATASETS_FRESH_MS = 5 * 60_000;

export function useDatasets() {
  const { api } = useSession();
  return useQuery({
    queryKey: ["datasets"],
    queryFn: ({ signal }) => fetchDatasets(api, signal),
    staleTime: DATASETS_FRESH_MS,
    // One more try, then say so: the user is waiting to choose.
    retry: 1,
  });
}

/** The section's title: for screen readers only on a wide screen, where the tab names it. */
export function LoadsHeading() {
  const { t } = useTranslation();
  const wide = useWideLayout();
  const title = <Title order={2}>{t("loads.title")}</Title>;
  return wide ? <VisuallyHidden>{title}</VisuallyHidden> : title;
}

export function StopLine({ stop, file, max }: { stop: UploadStop; file: File; max: number }) {
  const { t } = useTranslation();
  const text = {
    cancelled: () => t("loads.upload.cancelled"),
    broken: () => t("loads.upload.failed"),
    // The browser checked the size first: a refusal of a file under the limit
    // means the server's limit differs, and saying "it weighs X; the most is
    // more than X" would contradict itself.
    tooLarge: () =>
      file.size > max
        ? t("loads.file.tooLarge", { size: formatBytes(file.size), max: formatBytes(max) })
        : t("loads.upload.tooLargeForServer"),
    notXlsx: () => t("loads.file.notXlsx"),
    empty: () => t("loads.file.empty"),
    session: () => t("loads.upload.sessionRefused"),
    refused: () => t("loads.upload.refused", { code: stop.kind === "refused" ? stop.code : "" }),
  }[stop.kind]();
  const calm = stop.kind === "cancelled";
  return (
    <Text size="sm" role={calm ? "status" : "alert"} c={calm ? "dimmed" : "var(--ce-error)"}>
      {text}
    </Text>
  );
}

export function LoadsPage() {
  const { t } = useTranslation();
  const { api, auth } = useSession();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const reduced = useReducedMotion();
  const [search] = useSearchParams();
  const datasets = useDatasets();
  const [chosenKey, setChosenKey] = useState<string | null>(search.get("dataset"));
  const [file, setFile] = useState<File | null>(null);

  const onUploaded = useCallback(
    (draft: Draft) => {
      queryClient.setQueryData(["draft", draft.draft_id], draft);
      navigate(draftPath(draft.draft_id), { state: { validate: true } });
    },
    [navigate, queryClient],
  );
  const limits = datasets.data?.limits;
  const upload = useUpload({ auth, maxBytes: limits?.max_file_bytes ?? 0, onUploaded });

  const uploadable = (datasets.data?.datasets ?? []).filter((dataset) =>
    dataset.entry_modes.includes("upload"),
  );
  const chosen = uploadable.find((dataset) => dataset.key === chosenKey) ?? null;
  const busy = upload.state.phase !== "idle";

  return (
    <Box maw={960}>
      <Stack gap="lg">
        <Stack gap={4}>
          <LoadsHeading />
          <Text className={classes.secondary}>{t("loads.intro")}</Text>
        </Stack>

        {datasets.isPending && (
          <Stack gap="xs" aria-busy="true" aria-label={t("loads.dataset.loading")}>
            <Skeleton height={88} radius="md" animate={!reduced} />
            <Skeleton height={88} radius="md" animate={!reduced} />
          </Stack>
        )}

        {datasets.isError && (
          <Panel tone="error">
            <Stack gap="sm" align="flex-start">
              <Text>{t("loads.dataset.failed")}</Text>
              <Button variant="default" size="compact-sm" onClick={() => void datasets.refetch()}>
                {t("problem.retry")}
              </Button>
            </Stack>
          </Panel>
        )}

        {datasets.isSuccess && uploadable.length === 0 && (
          <Panel tone="info">
            <Text>{t("loads.dataset.none")}</Text>
          </Panel>
        )}

        {datasets.isSuccess && limits && uploadable.length > 0 && (
          <>
            <DatasetPicker
              api={api}
              datasets={uploadable}
              chosen={chosen}
              disabled={busy}
              onChoose={(dataset: Dataset) => setChosenKey(dataset.key)}
            />

            {chosen && (
              <Stack gap="sm">
                <Text fw={600} size="sm">
                  {t("loads.file.heading")}
                </Text>
                {upload.state.phase === "idle" ? (
                  <>
                    <FilePicker
                      limits={limits}
                      chosen={file}
                      disabled={busy}
                      onChoose={(next) => {
                        upload.forget();
                        setFile(next);
                      }}
                    />
                    {file && (
                      <Panel>
                        <Stack gap="sm">
                          <Group justify="space-between" wrap="wrap" gap="sm">
                            <Text fw={600} style={{ overflowWrap: "anywhere" }}>
                              {file.name}
                            </Text>
                            <Text size="sm" c="dimmed" className={classes.number}>
                              {formatBytes(file.size)}
                            </Text>
                          </Group>
                          {file.size > limits.validated_in_request_bytes && (
                            <Text size="sm" c="dimmed">
                              {t("loads.file.large", {
                                limit: formatBytes(limits.validated_in_request_bytes),
                              })}
                            </Text>
                          )}
                          {upload.state.stopped && (
                            <StopLine
                              stop={upload.state.stopped}
                              file={file}
                              max={limits.max_file_bytes}
                            />
                          )}
                          <Group>
                            <Button
                              onClick={() => void upload.start({ datasetKey: chosen.key }, file)}
                            >
                              {file.size > limits.validated_in_request_bytes
                                ? t("loads.file.uploadOnly")
                                : t("loads.file.upload")}
                            </Button>
                          </Group>
                        </Stack>
                      </Panel>
                    )}
                  </>
                ) : (
                  file && (
                    <UploadProgress file={file} state={upload.state} onCancel={upload.cancel} />
                  )
                )}
              </Stack>
            )}
          </>
        )}
      </Stack>
    </Box>
  );
}
