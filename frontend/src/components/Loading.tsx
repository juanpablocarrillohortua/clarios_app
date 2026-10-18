// What the app shows while it is finding out who the user is.
import { Center, Group, Loader, Text } from "@mantine/core";

import classes from "../styles/app.module.css";

export function Loading({ text }: { text: string }) {
  return (
    <Center mih="100dvh" p="md" className={classes.canvas}>
      <Group gap="sm" role="status">
        <Loader size="sm" />
        <Text c="dimmed">{text}</Text>
      </Group>
    </Center>
  );
}
