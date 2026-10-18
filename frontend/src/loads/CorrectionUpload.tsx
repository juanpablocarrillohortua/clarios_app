// "Subir archivo corregido": the one primary action of a draft whose file has
// problems. Data is corrected in Excel (ADR 0009); the corrected file goes up
// as a correction of this draft, and the page moves to the new draft, which is
// validated and compared with this one.
//
// The button opens the file chooser at once; a file dropped anywhere on the
// page does the same (DraftPage takes the drop). While the file goes up, the
// button's place holds its progress, and it can be cancelled at any moment.
// The file is checked first, as on the loading page, so a wrong one is
// refused before a byte is sent.
import { Button, Stack, Text } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import type { UploadLimits } from "../api/datasets";
import type { Draft } from "../api/drafts";
import { useSession } from "../auth/Session";
import { draftPath } from "../paths";
import { type FileProblem, fileProblem } from "./FilePicker";
import { formatBytes } from "./formatBytes";
import { StopLine } from "./LoadsPage";
import { UploadProgress } from "./UploadProgress";
import { useUpload } from "./useUpload";

/** The upload of a corrected file for one draft. */
export function useCorrection(draftId: string, limits: UploadLimits | undefined) {
  const { auth } = useSession();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<FileProblem | null>(null);
  const [same, setSame] = useState(false);

  const onUploaded = useCallback(
    (uploaded: Draft) => {
      // The same bytes as this draft: the server answers with this draft.
      if (uploaded.draft_id === draftId) {
        setSame(true);
        return;
      }
      queryClient.setQueryData(["draft", uploaded.draft_id], uploaded);
      navigate(draftPath(uploaded.draft_id), { state: { validate: true } });
    },
    [draftId, navigate, queryClient],
  );
  const upload = useUpload({ auth, maxBytes: limits?.max_file_bytes ?? 0, onUploaded });

  const choose = useCallback(
    (chosen: File | undefined) => {
      if (!chosen || !limits || upload.state.phase !== "idle") return;
      setSame(false);
      setFile(chosen);
      const kind = fileProblem(chosen, limits);
      setProblem(kind);
      upload.forget();
      if (!kind) void upload.start({ correcting: draftId }, chosen);
    },
    [draftId, limits, upload],
  );

  return { file, problem, same, upload, choose, busy: upload.state.phase !== "idle" };
}

export type Correction = ReturnType<typeof useCorrection>;

interface CorrectionUploadProps {
  correction: Correction;
  limits: UploadLimits;
  /** A file is being dragged over the page. */
  dragging: boolean;
}

export function CorrectionUpload({ correction, limits, dragging }: CorrectionUploadProps) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);
  const { file, problem, same, upload, choose } = correction;
  const state = upload.state;

  // A file dropped beside the page's content must not make the browser leave
  // the page to open it.
  useEffect(() => {
    const hold = (event: globalThis.DragEvent) => event.preventDefault();
    window.addEventListener("dragover", hold);
    window.addEventListener("drop", hold);
    return () => {
      window.removeEventListener("dragover", hold);
      window.removeEventListener("drop", hold);
    };
  }, []);

  if (state.phase !== "idle" && file) {
    return <UploadProgress file={file} state={state} onCancel={upload.cancel} />;
  }

  const max = formatBytes(limits.max_file_bytes);
  return (
    <Stack gap="xs" align="flex-start">
      <input
        ref={input}
        type="file"
        accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        hidden
        data-testid="correction-input"
        onChange={(event) => {
          choose(event.currentTarget.files?.[0]);
          // Choosing the same file again must still be noticed.
          event.currentTarget.value = "";
        }}
      />
      <Button onClick={() => input.current?.click()}>{t("loads.correction.upload")}</Button>
      <Text size="sm" c="dimmed">
        {dragging ? t("loads.correction.dropping") : t("loads.correction.hint")}
      </Text>
      {problem && file && (
        <Text size="sm" c="var(--ce-error)" role="alert">
          {problem === "tooLarge"
            ? t("loads.file.tooLarge", { size: formatBytes(file.size), max })
            : t(`loads.file.${problem}`)}
        </Text>
      )}
      {same && (
        <Text size="sm" role="status">
          {t("loads.correction.same")}
        </Text>
      )}
      {state.phase === "idle" && state.stopped && file && (
        <StopLine stop={state.stopped} file={file} max={limits.max_file_bytes} />
      )}
    </Stack>
  );
}
