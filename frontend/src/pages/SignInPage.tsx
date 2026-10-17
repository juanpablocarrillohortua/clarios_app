// Shown when nobody is signed in and the app did not go to Microsoft by
// itself: after signing out, or after a sign-in that failed. Nothing happens
// until the user presses the button, so a failure cannot turn into a loop.
import { Button } from "@mantine/core";
import { useTranslation } from "react-i18next";

import { Notice, NoticeLine } from "../components/Notice";

interface SignInPageProps {
  /** Why the app is asking: the user signed out, or sign-in did not complete. */
  reason: "signedOut" | "failed";
  /** The provider's code for the failure, when it gave one. */
  errorCode: string | null;
  onSignIn: () => void;
}

export function SignInPage({ reason, errorCode, onSignIn }: SignInPageProps) {
  const { t } = useTranslation();
  return (
    <Notice
      title={t("signIn.title")}
      tone={reason === "failed" ? "error" : "info"}
      action={<Button onClick={onSignIn}>{t("signIn.action")}</Button>}
    >
      <NoticeLine>{t(reason === "signedOut" ? "signIn.signedOut" : "signIn.failed")}</NoticeLine>
      {errorCode && <NoticeLine dimmed>{t("signIn.errorCode", { code: errorCode })}</NoticeLine>}
      <NoticeLine dimmed>{t("signIn.text")}</NoticeLine>
    </Notice>
  );
}
