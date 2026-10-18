// A page that is one message: a title, a few lines and, at most, one thing to
// do. Used wherever the app has nothing else to show: signed out, no access,
// the service unreachable.
import { Box, Center, Stack, Text, Title } from "@mantine/core";
import type { ReactNode } from "react";

import classes from "../styles/app.module.css";
import { Panel, type PanelTone } from "./Panel";

interface NoticeProps {
  title: string;
  /** What kind of message this is, shown as the panel's marker. */
  tone: PanelTone;
  /** The lines under the title, most important first. */
  children: ReactNode;
  /** A button, when there is something the user can do about it. */
  action?: ReactNode;
}

export function Notice({ title, tone, children, action }: NoticeProps) {
  return (
    <Center mih="100dvh" p="md" className={classes.canvas}>
      <Box component="main" w="100%" maw={480}>
        <Panel tone={tone}>
          <Stack gap="sm" align="flex-start">
            <Title order={1} size="h3">
              {title}
            </Title>
            {children}
            {action}
          </Stack>
        </Panel>
      </Box>
    </Center>
  );
}

/** One line of a notice. */
export function NoticeLine({
  children,
  dimmed = false,
}: {
  children: ReactNode;
  dimmed?: boolean;
}) {
  return (
    <Text
      size={dimmed ? "sm" : undefined}
      c={dimmed ? "dimmed" : undefined}
      className={dimmed ? undefined : classes.secondary}
    >
      {children}
    </Text>
  );
}
