// A conversation with the assistant, kept in the browser (docs/chatbot-plan.md,
// phase 3). The API stores nothing, so the conversation is this hook's state,
// mirrored in sessionStorage: a reload keeps it, closing the tab clears it. It
// is kept under the signed-in person's subject, so someone else signing in on
// the same tab starts with an empty one.
//
// A question shows at once, as a pending turn, and becomes part of the
// conversation only with its answer. The history sent with a question holds
// answered turns only. If the question fails, its text goes back into the box:
// nothing typed is lost. One question at a time: a ref, not the state, guards
// the sending, because two presses can arrive before React draws the disabled
// button (as loads/useCommit.ts does).
import { useCallback, useEffect, useRef, useState } from "react";

import { askChat, type ChatTurn, MAX_HISTORY } from "../api/chat";
import { ApiError } from "../api/client";
import { useSession } from "../auth/Session";

export type ChatFailure = "notConfigured" | "unavailable" | "unknown";

const FAILURE_OF_CODE: Partial<Record<string, ChatFailure>> = {
  chat_not_configured: "notConfigured",
  chat_model_unavailable: "unavailable",
};

export const STORAGE_KEY = "clarios.chat";

interface Saved {
  subject: string;
  turns: ChatTurn[];
}

function isTurn(value: unknown): value is ChatTurn {
  const turn = value as Partial<ChatTurn> | null;
  return (
    typeof turn === "object" &&
    turn !== null &&
    (turn.role === "user" || turn.role === "assistant") &&
    typeof turn.content === "string"
  );
}

function restore(subject: string): ChatTurn[] {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const saved = JSON.parse(raw) as Partial<Saved> | null;
    if (saved?.subject !== subject || !Array.isArray(saved.turns)) return [];
    return saved.turns.filter(isTurn).slice(-MAX_HISTORY);
  } catch {
    // Storage blocked, or something else under the key: start empty.
    return [];
  }
}

function keep(subject: string, turns: ChatTurn[]) {
  try {
    if (turns.length === 0) window.sessionStorage.removeItem(STORAGE_KEY);
    else window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ subject, turns }));
  } catch {
    // Storage blocked: the conversation lasts as long as the page.
  }
}

function failureOf(error: unknown): ChatFailure {
  if (error instanceof ApiError) return (error.code && FAILURE_OF_CODE[error.code]) || "unknown";
  return "unknown";
}

export interface Chat {
  /** The answered turns, oldest first. */
  turns: ChatTurn[];
  /** The question on its way, shown before its answer arrives. */
  pending: string | null;
  failure: ChatFailure | null;
  /** What is in the box. */
  draft: string;
  setDraft: (draft: string) => void;
  /** Send what is in the box. */
  send: () => void;
  /** Start again: forget the conversation and drop a question on its way. */
  reset: () => void;
}

export function useChat(): Chat {
  const { api, me } = useSession();
  const [turns, setTurns] = useState<ChatTurn[]>(() => restore(me.subject));
  const [pending, setPending] = useState<string | null>(null);
  const [failure, setFailure] = useState<ChatFailure | null>(null);
  const [draft, setDraft] = useState("");
  const inFlight = useRef<AbortController | null>(null);

  useEffect(() => keep(me.subject, turns), [me.subject, turns]);
  // Leaving the page drops a question on its way; its answer would land nowhere.
  useEffect(() => () => inFlight.current?.abort(), []);

  const send = useCallback(() => {
    if (inFlight.current || draft.trim() === "") return;
    const question = draft;
    const controller = new AbortController();
    inFlight.current = controller;
    setPending(question);
    setDraft("");
    setFailure(null);
    askChat(api, { history: turns.slice(-MAX_HISTORY), question }, controller.signal).then(
      (answer) => {
        if (controller.signal.aborted) return;
        inFlight.current = null;
        setPending(null);
        setTurns((earlier) =>
          [
            ...earlier,
            { role: "user" as const, content: question },
            { role: "assistant" as const, content: answer },
          ].slice(-MAX_HISTORY),
        );
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        inFlight.current = null;
        setPending(null);
        setFailure(failureOf(error));
        // Whatever was typed meanwhile stays, after the question.
        setDraft((typed) => (typed === "" ? question : `${question}\n${typed}`));
      },
    );
  }, [api, draft, turns]);

  const reset = useCallback(() => {
    inFlight.current?.abort();
    inFlight.current = null;
    setPending(null);
    setFailure(null);
    setTurns([]);
  }, []);

  return { turns, pending, failure, draft, setDraft, send, reset };
}
