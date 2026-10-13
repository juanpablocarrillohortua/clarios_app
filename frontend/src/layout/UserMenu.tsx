// The right end of the header: who is signed in, and the way out.
import { Button, Group, Text } from "@mantine/core";
import { useTranslation } from "react-i18next";

import { useSession } from "../auth/Session";

export function UserMenu() {
  const { t } = useTranslation();
  const { auth, me } = useSession();
  const name = me.name ?? me.username ?? "";

  return (
    <Group gap="sm" wrap="nowrap">
      {/* On a narrow screen the name gives its room to the title. */}
      <Text size="sm" visibleFrom="sm" data-testid="signed-in-as">
        {name}
      </Text>
      <Button variant="subtle" size="compact-sm" onClick={() => void auth.signOut()}>
        {t("user.signOut")}
      </Button>
    </Group>
  );
}
