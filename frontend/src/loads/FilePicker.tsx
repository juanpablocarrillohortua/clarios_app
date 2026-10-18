// Step two: the file. A drop zone that is also a button: a file dragged over
// it is answered at once, and a click or the keyboard opens the system's file
// chooser. On a phone, where nothing is dragged, it is just the button.
//
// The file is checked here before anything is sent, against the limits the
// server gives (GET /api/datasets): an .xlsx, not empty, not over the limit.
// The server checks again; this only spares the user a long wait for a "no".
import { Stack, Text } from "@mantine/core";
import { type DragEvent, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { UploadLimits } from "../api/datasets";
import { useWideLayout } from "../layout/useWideLayout";
import classes from "../styles/app.module.css";
import { formatBytes } from "./formatBytes";

export type FileProblem = "notXlsx" | "empty" | "tooLarge";

/** What is wrong with a file before it is sent, if anything. */
export function fileProblem(file: File, limits: UploadLimits): FileProblem | null {
  if (!file.name.toLowerCase().endsWith(".xlsx")) return "notXlsx";
  if (file.size === 0) return "empty";
  if (file.size > limits.max_file_bytes) return "tooLarge";
  return null;
}

interface FilePickerProps {
  limits: UploadLimits;
  chosen: File | null;
  onChoose: (file: File) => void;
  disabled: boolean;
}

export function FilePicker({ limits, chosen, onChoose, disabled }: FilePickerProps) {
  const { t } = useTranslation();
  const wide = useWideLayout();
  const input = useRef<HTMLInputElement>(null);
  const hintId = useId();
  const problemId = useId();
  // Entering a child of the zone fires enter and leave in pairs; counting them
  // keeps the highlight steady while the file is over any part of it.
  const depth = useRef(0);
  const [dragging, setDragging] = useState(false);
  const [problem, setProblem] = useState<{ kind: FileProblem; file: File } | null>(null);

  // A file dropped beside the zone must not make the browser leave the page
  // to open it: the chosen dataset and file would be lost.
  useEffect(() => {
    const hold = (event: globalThis.DragEvent) => event.preventDefault();
    window.addEventListener("dragover", hold);
    window.addEventListener("drop", hold);
    return () => {
      window.removeEventListener("dragover", hold);
      window.removeEventListener("drop", hold);
    };
  }, []);

  function take(file: File | undefined) {
    if (!file) return;
    const kind = fileProblem(file, limits);
    if (kind) {
      setProblem({ kind, file });
      return;
    }
    setProblem(null);
    onChoose(file);
  }

  function onDragEnter(event: DragEvent) {
    event.preventDefault();
    if (disabled) return;
    depth.current += 1;
    setDragging(true);
  }

  function onDragLeave(event: DragEvent) {
    event.preventDefault();
    depth.current = Math.max(0, depth.current - 1);
    if (depth.current === 0) setDragging(false);
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    depth.current = 0;
    setDragging(false);
    if (!disabled) take(event.dataTransfer.files[0]);
  }

  const max = formatBytes(limits.max_file_bytes);
  const label = chosen ? t("loads.file.chooseAnother") : t("loads.file.choose");

  return (
    <Stack gap="xs">
      <input
        ref={input}
        type="file"
        accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        hidden
        data-testid="file-input"
        onChange={(event) => {
          take(event.currentTarget.files?.[0]);
          // Choosing the same file again must still be noticed.
          event.currentTarget.value = "";
        }}
      />
      <button
        type="button"
        className={classes.dropZone}
        data-dragging={dragging || undefined}
        data-invalid={problem ? true : undefined}
        disabled={disabled}
        aria-describedby={problem ? `${hintId} ${problemId}` : hintId}
        onClick={() => input.current?.click()}
        onDragEnter={onDragEnter}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        <Text fw={600} c="var(--ce-text)">
          {wide ? (dragging ? t("loads.file.dropping") : t("loads.file.drop")) : label}
        </Text>
        {wide && !dragging && (
          <Text size="sm" className={classes.secondary}>
            {label}
          </Text>
        )}
        <Text size="xs" c="dimmed" id={hintId}>
          {t("loads.file.limits", { max })}
        </Text>
      </button>
      {problem && (
        <Text size="sm" c="var(--ce-error)" id={problemId} role="alert">
          {problem.kind === "tooLarge"
            ? t("loads.file.tooLarge", { size: formatBytes(problem.file.size), max })
            : t(`loads.file.${problem.kind}`)}
        </Text>
      )}
    </Stack>
  );
}
