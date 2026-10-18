// What a part of a page shows while it waits: a small spinner and one line,
// as one status for screen readers. With reduced motion the spinner is left
// out: the line says it all, and nothing turns on the screen.
import { Group, Loader, Text } from "@mantine/core";
import { useReducedMotion } from "@mantine/hooks";

export function Busy({ text }: { text: string }) {
  const reduced = useReducedMotion();
  return (
    <Group gap="sm" role="status" wrap="nowrap">
      {reduced ? null : <Loader size="xs" />}
      <Text size="sm" c="dimmed">
        {text}
      </Text>
    </Group>
  );
}
