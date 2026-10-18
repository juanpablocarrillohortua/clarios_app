// Confirming a draft: one request, however many times the button is pressed.
// A ref, not the state, guards it, because two presses can arrive before React
// draws the disabled button. The server keeps the same promise on its side: a
// draft commits once, and asking again answers with its load.
//
// Every refusal is named, so the page can say what to do. One case is not a
// refusal: the connection broke before the answer. Whether the load landed is
// then unknown, and asking again ("Comprobar") is safe, because a committed
// draft answers with its load.
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { type CommitRequest, commitDraft } from "../api/drafts";
import type { LoadDetail } from "../api/loads";
import { useSession } from "../auth/Session";

export type CommitFailure =
  | "validationChanged"
  | "currentVersionChanged"
  | "datasetBusy"
  | "draftBusy"
  | "originalMissing"
  | "alreadyLoaded"
  | "unknown"
  | "failed";

export type CommitState =
  | { phase: "idle" }
  | { phase: "committing" }
  | { phase: "failed"; failure: CommitFailure };

const FAILURE_OF_CODE: Partial<Record<string, CommitFailure>> = {
  validation_changed: "validationChanged",
  current_version_changed: "currentVersionChanged",
  dataset_busy: "datasetBusy",
  draft_busy: "draftBusy",
  original_missing: "originalMissing",
  already_loaded: "alreadyLoaded",
};

/** What a refused or lost commit means for the person. */
export function failureOf(error: unknown): CommitFailure {
  if (error instanceof ApiError) {
    return (error.code && FAILURE_OF_CODE[error.code]) || "failed";
  }
  // fetch rejects without an answer: the request may or may not have arrived.
  return "unknown";
}

/** Refusals after which what the page shows is out of date. */
const STALE: ReadonlySet<CommitFailure> = new Set([
  "validationChanged",
  "currentVersionChanged",
  "alreadyLoaded",
  "failed",
]);

export function useCommit(draftId: string, onCommitted: (load: LoadDetail) => void) {
  const { api } = useSession();
  const queryClient = useQueryClient();
  const inFlight = useRef(false);
  const [state, setState] = useState<CommitState>({ phase: "idle" });

  const commit = useCallback(
    async (request: CommitRequest) => {
      if (inFlight.current) return;
      inFlight.current = true;
      setState({ phase: "committing" });
      try {
        const sent = await commitDraft(api, draftId, request);
        setState({ phase: "idle" });
        onCommitted(sent.body);
      } catch (error) {
        const failure = failureOf(error);
        setState({ phase: "failed", failure });
        if (STALE.has(failure)) {
          void queryClient.invalidateQueries({ queryKey: ["confirmation", draftId] });
          void queryClient.invalidateQueries({ queryKey: ["draft", draftId] });
        }
      } finally {
        inFlight.current = false;
      }
    },
    [api, draftId, onCommitted, queryClient],
  );

  return { state, commit, committing: state.phase === "committing" };
}
