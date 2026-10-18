// Shown to a user who signed in but holds no role: the API granted nothing.
// Access is given in Entra ID, by assigning the account a role of the app.
import { Button } from "@mantine/core";
import { useTranslation } from "react-i18next";

import { Notice, NoticeLine } from "../components/Notice";

interface NoAccessPageProps {
  /** The account that is signed in, so the user can tell an administrator. */
  account: string;
  onSignOut: () => void;
}

export function NoAccessPage({ account, onSignOut }: NoAccessPageProps) {
  const { t } = useTranslation();
  return (
    <Notice
      title={t("noAccess.title")}
      tone="info"
      action={
        <Button variant="default" onClick={onSignOut}>
          {t("user.signOut")}
        </Button>
      }
    >
      <NoticeLine>{t("noAccess.text")}</NoticeLine>
      <NoticeLine dimmed>{t("noAccess.account", { account })}</NoticeLine>
    </Notice>
  );
}
