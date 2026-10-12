// What a section shows until it is built: its title and one line on what will
// be there.
import { Stack, Text, Title } from "@mantine/core";

interface SectionPlaceholderProps {
  title: string;
  text: string;
}

export function SectionPlaceholder({ title, text }: SectionPlaceholderProps) {
  return (
    <Stack gap="xs">
      <Title order={2}>{title}</Title>
      <Text c="dimmed">{text}</Text>
    </Stack>
  );
}
