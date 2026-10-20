// The conversation: the person's turns on the right, the assistant's on the
// left, as any chat. Every text is plain text, never HTML: an answer is shown
// as written, whatever it holds.
//
// Before the first question the log offers a few example questions. Choosing
// one fills the box and sends nothing, so the person stays in control.
//
// The log follows a new message only when the reader is already at its foot:
// someone reading back is not pulled down. Their own question always brings
// them down, because they just acted.
import { Button, Group, Text, VisuallyHidden } from "@mantine/core";
import { useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import type { ChatTurn } from "../api/chat";
import classes from "./chat.module.css";

// How close to the foot, in pixels, still counts as "at the foot".
const FOOT_SLACK_PX = 24;

const EXAMPLES = [
  "dashboard.chat.examples.equsMonth",
  "dashboard.chat.examples.topProcess",
  "dashboard.chat.examples.trend",
] as const;

interface ChatLogProps {
  turns: ChatTurn[];
  pending: string | null;
  /** Whether the wait has lasted long enough to show that the assistant is at work. */
  thinking: boolean;
  onExample: (question: string) => void;
}

export function ChatLog({ turns, pending, thinking, onExample }: ChatLogProps) {
  const { t } = useTranslation();
  const log = useRef<HTMLDivElement>(null);
  const atFoot = useRef(true);
  const asked = useRef<string | null>(null);

  useLayoutEffect(() => {
    const element = log.current;
    if (!element) return;
    const justAsked = pending !== null && asked.current === null;
    asked.current = pending;
    if (atFoot.current || justAsked) element.scrollTop = element.scrollHeight;
  });

  const onScroll = () => {
    const element = log.current;
    if (!element) return;
    atFoot.current =
      element.scrollHeight - element.scrollTop - element.clientHeight <= FOOT_SLACK_PX;
  };

  const speaker = (turn: ChatTurn) =>
    turn.role === "user" ? t("dashboard.chat.you") : t("dashboard.chat.assistant");

  return (
    <div
      ref={log}
      className={classes.log}
      role="log"
      aria-live="polite"
      aria-label={t("dashboard.chat.conversation")}
      onScroll={onScroll}
    >
      {turns.length === 0 && pending === null ? (
        <div className={classes.empty}>
          <Text size="sm" c="dimmed">
            {t("dashboard.chat.empty")}
          </Text>
          <Group gap="xs">
            {EXAMPLES.map((key) => (
              <Button
                key={key}
                variant="default"
                size="xs"
                radius="xl"
                className={classes.example}
                onClick={() => onExample(t(key))}
              >
                {t(key)}
              </Button>
            ))}
          </Group>
        </div>
      ) : (
        <ol className={classes.turns}>
          {turns.map((turn, index) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: turns are only appended or all cleared
            <li key={`turn-${index}`} className={classes.turn} data-role={turn.role}>
              <VisuallyHidden>{speaker(turn)}: </VisuallyHidden>
              <p className={classes.bubble}>{turn.content}</p>
            </li>
          ))}
          {/* The question on its way takes the key its answered turn will
              have, so it is not drawn (nor enters) again when answered. */}
          {pending !== null && (
            <li key={`turn-${turns.length}`} className={classes.turn} data-role="user" data-pending>
              <VisuallyHidden>{t("dashboard.chat.you")}: </VisuallyHidden>
              <p className={classes.bubble}>{pending}</p>
            </li>
          )}
          {thinking && (
            <li key="thinking" className={classes.turn} data-role="assistant">
              <p className={classes.bubble} role="status">
                <span className={classes.dots} aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </span>
                <span className={classes.thinkingText}>{t("dashboard.chat.pending")}</span>
              </p>
            </li>
          )}
        </ol>
      )}
    </div>
  );
}
