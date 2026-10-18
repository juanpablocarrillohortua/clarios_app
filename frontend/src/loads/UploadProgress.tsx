// An upload on its way: the file, a bar that follows the bytes sent, how much
// and how long remains, and a way to stop it at any moment. Once every byte is
// sent the server is still storing the file: the bar is full and a line says
// so, and there is nothing left to cancel.
//
// Screen readers hear the progress in steps of ten per cent, not on every
// event, and hear when the file is being stored.
import { Button, Group, Stack, Text, VisuallyHidden } from "@mantine/core";
import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";

import { Busy } from "../components/Busy";
import { Panel } from "../components/Panel";
import classes from "../styles/app.module.css";
import { formatBytes } from "./formatBytes";
import type { UploadState } from "./useUpload";

const MINUTE_MS = 60_000;

interface UploadProgressProps {
  file: File;
  state: Extract<UploadState, { phase: "sending" | "archiving" }>;
  onCancel: () => void;
}

export function UploadProgress({ file, state, onCancel }: UploadProgressProps) {
  const { t } = useTranslation();
  const sending = state.phase === "sending";
  const fraction = sending ? (state.total > 0 ? state.sent / state.total : 0) : 1;
  const percent = Math.floor(fraction * 100);
  const announced = Math.floor(percent / 10) * 10;

  let remaining: string | null = null;
  if (sending && state.remainingMs !== null) {
    const minutes = Math.round(state.remainingMs / MINUTE_MS);
    remaining =
      minutes < 1
        ? t("loads.upload.remainingSoon")
        : t("loads.upload.remainingMinutes", { count: minutes });
  }

  return (
    <Panel>
      <Stack gap="sm">
        <Group justify="space-between" wrap="nowrap" gap="sm">
          <Text fw={600} truncate>
            {file.name}
          </Text>
          {sending && (
            <Button variant="default" size="compact-sm" onClick={onCancel}>
              {t("loads.upload.cancel")}
            </Button>
          )}
        </Group>
        <div
          className={classes.progressTrack}
          role="progressbar"
          aria-label={t("loads.upload.label")}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
        >
          <div
            className={classes.progressFill}
            style={{ "--progress": fraction } as CSSProperties}
          />
        </div>
        {sending ? (
          <Text size="sm" className={classes.status}>
            {t("loads.upload.progress", {
              sent: formatBytes(state.sent),
              total: formatBytes(state.total),
              percent,
            })}
            {remaining ? ` · ${remaining}` : ""}
          </Text>
        ) : (
          <Busy text={t("loads.upload.archiving")} />
        )}
        <VisuallyHidden aria-live="polite">
          {sending ? t("loads.upload.announce", { percent: announced }) : ""}
        </VisuallyHidden>
      </Stack>
    </Panel>
  );
}
