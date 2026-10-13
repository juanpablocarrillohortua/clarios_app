// A page that is one message: a title, a few lines and, at most, one thing to
// do. Used wherever the app has nothing else to show: signed out, no access,
// the service unreachable.
import { Center, Stack, Text, Title } from "@mantine/core";
import type { ReactNode } from "react";

interface NoticeProps {
  title: string;
  /** The lines under the title, most important first. */
  children: ReactNode;
  /** A button, when there is something the user can do about it. */
  action?: ReactNode;
}

export function Notice({ title, children, action }: NoticeProps) {
  return (
    <Center mih="100dvh" p="md">
      <Stack component="main" gap="sm" maw={480} align="flex-start">
        <Title order={1} size="h3">
          {title}
        </Title>
        {children}
        {action}
      </Stack>
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
    <Text size={dimmed ? "sm" : undefined} c={dimmed ? "dimmed" : undefined}>
      {children}
    </Text>
  );
}
