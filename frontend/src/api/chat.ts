// The chat assistant (src/energy/api/routers/chat.py): one question, sent with
// the conversation so far. The API stores nothing: the conversation lives in
// the browser (chat/useChat.ts).
import type { Api } from "./client";

/** The most earlier messages a question may carry: MAX_CHAT_HISTORY of the API. */
export const MAX_HISTORY = 200;
/** The longest question the API accepts: MAX_QUESTION_CHARS of the API. */
export const MAX_QUESTION_CHARS = 4_000;

export type ChatRole = "user" | "assistant";

export interface ChatTurn {
  role: ChatRole;
  content: string;
}

export interface ChatRequest {
  /** The earlier messages, oldest first, without the question. */
  history: ChatTurn[];
  question: string;
}

export interface ChatAnswer {
  /** Plain text: never rendered as HTML. */
  answer: string;
}

export async function askChat(
  api: Api,
  request: ChatRequest,
  signal?: AbortSignal,
): Promise<string> {
  const sent = await api.postJson<ChatAnswer>("/chat", request, signal);
  return sent.body.answer;
}
