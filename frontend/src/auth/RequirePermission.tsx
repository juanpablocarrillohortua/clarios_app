// Wraps a section's page: the page is drawn only for a user who holds the
// section's permission. This is courtesy, not protection: the API refuses the
// same user on its own routes whatever the browser shows.
import { Anchor, Stack, Text, Title } from "@mantine/core";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import type { Permission } from "../sections";
import { useSession } from "./Session";

interface RequirePermissionProps {
  permission: Permission;
  children: ReactNode;
}

export function RequirePermission({ permission, children }: RequirePermissionProps) {
  const { t } = useTranslation();
  const { me } = useSession();

  if (me.permissions.includes(permission)) {
    return children;
  }
  return (
    <Stack gap="xs">
      <Title order={2}>{t("forbidden.title")}</Title>
      <Text c="dimmed">{t("forbidden.text")}</Text>
      <Anchor component={Link} to="/">
        {t("forbidden.back")}
      </Anchor>
    </Stack>
  );
}
