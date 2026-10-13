// What the app shows while it is finding out who the user is.
import { Center, Group, Loader, Text } from "@mantine/core";

export function Loading({ text }: { text: string }) {
  return (
    <Center mih="100dvh" p="md">
      <Group gap="sm" role="status">
        <Loader size="sm" />
        <Text c="dimmed">{text}</Text>
      </Group>
    </Center>
  );
}
