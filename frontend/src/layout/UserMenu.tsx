// The right end of the header: who is signed in, and the way out.
import { Button, Group, Text } from "@mantine/core";
import { useTranslation } from "react-i18next";

import { useSession } from "../auth/Session";
import classes from "../styles/app.module.css";

export function UserMenu() {
  const { t } = useTranslation();
  const { auth, me } = useSession();
  const name = me.name ?? me.username ?? "";

  return (
    <Group gap="sm" wrap="nowrap">
      {/* On a narrower screen the name gives its room to the title and the tabs. */}
      <Text size="sm" visibleFrom="md" className={classes.secondary} data-testid="signed-in-as">
        {name}
      </Text>
      <Button variant="default" size="compact-sm" onClick={() => void auth.signOut()}>
        {t("user.signOut")}
      </Button>
    </Group>
  );
}
