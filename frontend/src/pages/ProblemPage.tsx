// Shown when the app cannot start a session, for a reason that is not the
// user's doing. Each kind says what happened and, where it helps, offers one
// thing to do.
import { Button } from "@mantine/core";
import { useTranslation } from "react-i18next";

import { Notice, NoticeLine } from "../components/Notice";
import type { PanelTone } from "../components/Panel";

export type ProblemKind =
  /** The server was not given its sign-in settings. */
  | "notConfigured"
  /** The API did not answer, or answered an error. */
  | "unreachable"
  /** The browser could not prepare sign-in, for example on an address that is not secure. */
  | "cannotStart"
  /** The API refused the token the user signed in with. */
  | "sessionRefused";

// A server not given its settings is a state to put right, not a failure.
const TONE: Record<ProblemKind, PanelTone> = {
  notConfigured: "info",
  unreachable: "error",
  cannotStart: "error",
  sessionRefused: "error",
};

interface ProblemPageProps {
  kind: ProblemKind;
  /** Try again; offered for "unreachable". */
  onRetry?: () => void;
  /** Sign in again; offered for "sessionRefused". */
  onSignIn?: () => void;
}

export function ProblemPage({ kind, onRetry, onSignIn }: ProblemPageProps) {
  const { t } = useTranslation();
  const action =
    kind === "unreachable" && onRetry ? (
      <Button variant="default" onClick={onRetry}>
        {t("problem.retry")}
      </Button>
    ) : kind === "sessionRefused" && onSignIn ? (
      <Button onClick={onSignIn}>{t("signIn.action")}</Button>
    ) : undefined;

  return (
    <Notice title={t(`problem.${kind}.title`)} tone={TONE[kind]} action={action}>
      <NoticeLine>{t(`problem.${kind}.text`)}</NoticeLine>
    </Notice>
  );
}
