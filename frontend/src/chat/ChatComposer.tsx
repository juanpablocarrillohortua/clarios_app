// Where the person writes. Enter sends and Shift+Enter starts a new line, as
// in most chats. The box is never locked: the next question can be typed while
// the assistant answers; only "Enviar" waits. A failure is said right under
// the box it concerns, and the question is back in it.
import { Button, Text, Textarea } from "@mantine/core";
import type { KeyboardEvent, Ref } from "react";
import { useTranslation } from "react-i18next";

import { MAX_QUESTION_CHARS } from "../api/chat";
import classes from "./chat.module.css";
import type { ChatFailure } from "./useChat";

const FAILURE_TEXT = {
  notConfigured: "dashboard.chat.failure.notConfigured",
  unavailable: "dashboard.chat.failure.unavailable",
  unknown: "dashboard.chat.failure.unknown",
} as const satisfies Record<ChatFailure, string>;

interface ChatComposerProps {
  box: Ref<HTMLTextAreaElement>;
  draft: string;
  onDraft: (draft: string) => void;
  sending: boolean;
  failure: ChatFailure | null;
  onSend: () => void;
}

export function ChatComposer({ box, draft, onDraft, sending, failure, onSend }: ChatComposerProps) {
  const { t } = useTranslation();

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // A key that ends an input method's composition is not a send.
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    onSend();
  };

  return (
    <div className={classes.composer}>
      <form
        className={classes.form}
        onSubmit={(event) => {
          event.preventDefault();
          onSend();
        }}
      >
        <Textarea
          ref={box}
          className={classes.box}
          value={draft}
          onChange={(event) => onDraft(event.currentTarget.value)}
          onKeyDown={onKeyDown}
          aria-label={t("dashboard.chat.question")}
          placeholder={t("dashboard.chat.placeholder")}
          maxLength={MAX_QUESTION_CHARS}
          autosize
          minRows={1}
          maxRows={5}
        />
        <Button type="submit" className={classes.send} disabled={sending || draft.trim() === ""}>
          {t("dashboard.chat.send")}
        </Button>
      </form>
      {failure && (
        <Text role="alert" size="sm" c="var(--ce-error)">
          {t(FAILURE_TEXT[failure])}
        </Text>
      )}
      <Text size="xs" c="dimmed" className={classes.disclaimer}>
        {t("dashboard.chat.disclaimer")}
      </Text>
    </div>
  );
}
