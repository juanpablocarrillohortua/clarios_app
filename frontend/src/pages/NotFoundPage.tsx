// Shown for an address that matches no section.
import { Anchor, Stack, Text, Title } from "@mantine/core";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { paths } from "../paths";

export function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <Stack gap="xs">
      <Title order={2}>{t("notFound.title")}</Title>
      <Text c="dimmed">{t("notFound.text")}</Text>
      <Anchor component={Link} to={paths.dashboard}>
        {t("notFound.back")}
      </Anchor>
    </Stack>
  );
}
