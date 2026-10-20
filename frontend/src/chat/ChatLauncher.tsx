// The chat assistant on the dashboard (docs/chatbot-plan.md, phase 3): a round
// button in the bottom-right corner that opens a panel above it. The panel is
// a parallel tool, not a dialog: no scrim, the report stays usable beside it,
// and a click elsewhere does not close it. Only the button, Escape and the
// panel's own ✕ do.
//
// The panel grows out of the button and closes back into it, on the app's
// spring (chat.module.css). Its transitions follow a state attribute, so a
// second press while it moves turns it round from where it is. Closed, it is
// inert and hidden from screen readers, and it stays mounted, so the motion
// has something to close.
//
// The conversation lives here, outside the panel: closing the panel while a
// question is on its way changes nothing. If the answer arrives while it is
// closed, a dot on the button says so until the panel opens again.
import { Button, CloseButton, Group, Text, VisuallyHidden } from "@mantine/core";
import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useDelayed } from "../hooks/useDelayed";
import { ChatComposer } from "./ChatComposer";
import { ChatLog } from "./ChatLog";
import classes from "./chat.module.css";
import { useChat } from "./useChat";

/** How long a question waits before the log shows the assistant at work. */
export const THINKING_AFTER_MS = 300;

function BubbleIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className={classes.glyph} data-glyph="bubble">
      <path d="M20 11.5a7.5 7.5 0 0 1-10.9 6.7L4.5 19.5l1.2-4.1A7.5 7.5 0 1 1 20 11.5Z" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className={classes.glyph} data-glyph="close">
      <path d="M7 7l10 10M17 7L7 17" />
    </svg>
  );
}

export function ChatLauncher() {
  const { t } = useTranslation();
  const chat = useChat();
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(false);
  const launcher = useRef<HTMLButtonElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const panelId = useId();
  const thinking = useDelayed(chat.pending !== null, THINKING_AFTER_MS);

  // An answer that lands while the panel is closed is news.
  const answered = useRef(chat.turns.length);
  useEffect(() => {
    if (chat.turns.length > answered.current && !open) setUnread(true);
    answered.current = chat.turns.length;
  }, [chat.turns.length, open]);

  useEffect(() => {
    if (open) {
      setUnread(false);
      box.current?.focus({ preventScroll: true });
    }
  }, [open]);

  // Focus goes back to the button before the panel turns inert, so it is
  // never lost with the panel.
  const close = () => {
    launcher.current?.focus();
    setOpen(false);
  };

  const onPanelKey = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    close();
  };

  const fill = (question: string) => {
    chat.setDraft(question);
    box.current?.focus();
  };

  const started = chat.turns.length > 0 || chat.pending !== null;

  return (
    <>
      <section
        id={panelId}
        className={classes.panel}
        data-open={open || undefined}
        aria-label={t("dashboard.chat.heading")}
        aria-hidden={!open}
        inert={!open}
        onKeyDown={onPanelKey}
      >
        <Group className={classes.panelHeader} justify="space-between" wrap="nowrap" gap="xs">
          <Text component="h2" className={classes.panelTitle}>
            {t("dashboard.chat.heading")}
          </Text>
          <Group gap={4} wrap="nowrap">
            {started && (
              <Button
                variant="subtle"
                size="compact-sm"
                onClick={() => {
                  chat.reset();
                  box.current?.focus();
                }}
              >
                {t("dashboard.chat.newConversation")}
              </Button>
            )}
            <CloseButton aria-label={t("dashboard.chat.close")} onClick={close} />
          </Group>
        </Group>
        <ChatLog turns={chat.turns} pending={chat.pending} thinking={thinking} onExample={fill} />
        <ChatComposer
          box={box}
          draft={chat.draft}
          onDraft={chat.setDraft}
          sending={chat.pending !== null}
          failure={chat.failure}
          onSend={() => {
            // "Enviar" turns disabled while the answer comes, which would drop
            // the focus: it goes back to the box, ready for the next question.
            chat.send();
            box.current?.focus();
          }}
        />
      </section>
      <button
        ref={launcher}
        type="button"
        className={classes.launcher}
        data-open={open || undefined}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={open ? t("dashboard.chat.close") : t("dashboard.chat.open")}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <BubbleIcon />
        <CloseIcon />
        {unread && <span className={classes.unread} aria-hidden="true" />}
      </button>
      {/* The dot is for the eye; this says the same to a screen reader. A
          live region must be on the page before its words change. */}
      <VisuallyHidden role="status">{unread ? t("dashboard.chat.unread") : ""}</VisuallyHidden>
    </>
  );
}
