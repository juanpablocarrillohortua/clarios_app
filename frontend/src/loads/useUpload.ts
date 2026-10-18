// One upload at a time: its progress, its cancel, and what became of it.
//
// The progress is what the browser reports as the bytes leave it. Once every
// byte is sent the server still has to finish storing the file, so the upload
// shows "archiving" until the answer arrives. Cancelling works at any moment:
// it aborts the request, the server removes what it had received, and the
// chosen file stays chosen. The same hook sends a new file and a corrected
// one: only where it goes differs.
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { type Draft, type UploadTarget, uploadDraft } from "../api/drafts";
import { SendAborted, SendBroken } from "../api/upload";
import type { AuthClient } from "../auth/authClient";

/** Why an upload did not end with a draft. */
export type UploadStop =
  | { kind: "cancelled" }
  | { kind: "broken" }
  | { kind: "tooLarge" }
  | { kind: "notXlsx" }
  | { kind: "empty" }
  | { kind: "session" }
  | { kind: "refused"; code: string };

export type UploadState =
  | { phase: "idle"; stopped: UploadStop | null }
  | { phase: "sending"; sent: number; total: number; remainingMs: number | null }
  | { phase: "archiving" };

// The rate is not trusted before this much of the upload has been seen.
const RATE_AFTER_MS = 3_000;

function stopOf(error: unknown, file: File, maxBytes: number): UploadStop {
  if (error instanceof SendAborted) return { kind: "cancelled" };
  if (error instanceof SendBroken) {
    // A proxy that refuses a body too large may simply cut the connection.
    return file.size > maxBytes ? { kind: "tooLarge" } : { kind: "broken" };
  }
  if (error instanceof ApiError) {
    if (error.status === 401) return { kind: "session" };
    if (error.status === 413 || error.code === "file_too_large") return { kind: "tooLarge" };
    if (error.code === "empty_file") return { kind: "empty" };
    if (error.status === 415) return { kind: "notXlsx" };
    return { kind: "refused", code: error.code ?? String(error.status) };
  }
  return { kind: "broken" };
}

interface UploadOptions {
  auth: AuthClient;
  /** The largest file the server accepts, from GET /api/datasets. */
  maxBytes: number;
  /** Called with the draft once the server has stored the file. */
  onUploaded: (draft: Draft) => void;
}

export function useUpload({ auth, maxBytes, onUploaded }: UploadOptions) {
  const [state, setState] = useState<UploadState>({ phase: "idle", stopped: null });
  const controller = useRef<AbortController | null>(null);

  // Leaving the page stops an upload still running.
  useEffect(() => () => controller.current?.abort(), []);

  const start = useCallback(
    async (target: UploadTarget, file: File) => {
      controller.current?.abort();
      const current = new AbortController();
      controller.current = current;
      const started = performance.now();
      setState({ phase: "sending", sent: 0, total: file.size, remainingMs: null });
      try {
        const draft = await uploadDraft(
          auth,
          target,
          file,
          ({ sent, total }) => {
            if (current.signal.aborted) return;
            if (sent >= total) {
              setState({ phase: "archiving" });
              return;
            }
            const elapsed = performance.now() - started;
            const remainingMs =
              elapsed >= RATE_AFTER_MS && sent > 0 ? ((total - sent) * elapsed) / sent : null;
            setState({ phase: "sending", sent, total, remainingMs });
          },
          current.signal,
        );
        if (controller.current === current) {
          controller.current = null;
          setState({ phase: "idle", stopped: null });
          onUploaded(draft);
        }
      } catch (error) {
        if (controller.current === current) {
          controller.current = null;
          setState({ phase: "idle", stopped: stopOf(error, file, maxBytes) });
        }
      }
    },
    [auth, maxBytes, onUploaded],
  );

  const cancel = useCallback(() => controller.current?.abort(), []);

  const forget = useCallback(() => {
    setState((previous) =>
      previous.phase === "idle" ? { phase: "idle", stopped: null } : previous,
    );
  }, []);

  return { state, start, cancel, forget };
}
