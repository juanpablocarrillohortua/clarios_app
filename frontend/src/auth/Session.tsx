// Starts the user's session and hands it to the pages. Nothing of the app is
// drawn until three things are known, in this order:
//
//   1. the sign-in settings, from GET /api/config, which also names the
//      Power BI report of the dashboard;
//   2. who is signed in in this tab; with nobody, the browser goes to Microsoft;
//   3. what that person may do, from GET /api/me.
//
// Each step that cannot be completed has a page that says so. The pages inside
// read the result with useSession().
import { useQuery } from "@tanstack/react-query";
import { createContext, type ReactNode, useContext, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";

import { type Api, ApiError, createApi } from "../api/client";
import { type AuthConfig, fetchClientConfig, type PowerBiConfig } from "../api/config";
import { fetchMe, type Me } from "../api/me";
import { Loading } from "../components/Loading";
import { NoAccessPage } from "../pages/NoAccessPage";
import { ProblemPage } from "../pages/ProblemPage";
import { SignInPage } from "../pages/SignInPage";
import { paths } from "../paths";
import type { Account, AuthClient, CreateAuthClient } from "./authClient";

export interface SessionValue {
  auth: AuthClient;
  /** Calls the API as the signed-in user. */
  api: Api;
  /** The API's answer about the user: name, roles and permissions. */
  me: Me;
  /** The report the dashboard shows; null when the server names none. */
  powerBi: PowerBiConfig | null;
}

const SessionContext = createContext<SessionValue | null>(null);

/** The session of the signed-in user. Only for components inside <Session>. */
export function useSession(): SessionValue {
  const session = useContext(SessionContext);
  if (!session) {
    throw new Error("useSession was called outside <Session>");
  }
  return session;
}

// How long the API's answer about the user is trusted before asking again.
const ME_FRESH_FOR_MS = 5 * 60_000;

// Remembers, for this tab, that the app already sent the browser to Microsoft.
// Coming back without an account then shows the sign-in page, where nothing
// happens until the user presses a button: a failure cannot become a loop.
const SIGN_IN_STARTED = "clarios.signInStarted";

function signInWasStarted(): boolean {
  try {
    return window.sessionStorage.getItem(SIGN_IN_STARTED) !== null;
  } catch {
    return false;
  }
}

function noteSignInStarted(started: boolean): void {
  try {
    if (started) {
      window.sessionStorage.setItem(SIGN_IN_STARTED, "1");
    } else {
      window.sessionStorage.removeItem(SIGN_IN_STARTED);
    }
  } catch {
    // Storage is blocked: sign-in itself would not work either.
  }
}

interface SessionProps {
  createAuthClient: CreateAuthClient;
  children: ReactNode;
}

export function Session({ createAuthClient, children }: SessionProps) {
  const { t } = useTranslation();
  const config = useQuery({
    queryKey: ["client-config"],
    queryFn: ({ signal }) => fetchClientConfig(signal),
    // The settings do not change while the page is open.
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });

  if (config.isPending || (config.isError && config.isFetching)) {
    return <Loading text={t("session.starting")} />;
  }
  if (config.isError) {
    return <ProblemPage kind="unreachable" onRetry={() => void config.refetch()} />;
  }
  if (config.data.auth === null) {
    return <ProblemPage kind="notConfigured" />;
  }
  return (
    <WithAuthClient
      config={config.data.auth}
      powerBi={config.data.powerbi}
      createAuthClient={createAuthClient}
    >
      {children}
    </WithAuthClient>
  );
}

interface WithAuthClientProps extends SessionProps {
  config: AuthConfig;
  powerBi: PowerBiConfig | null;
}

function WithAuthClient({ config, powerBi, createAuthClient, children }: WithAuthClientProps) {
  const { t } = useTranslation();
  // A query, so the client is built once however often React renders: building
  // it reads the answer Microsoft left, which can be read only once.
  const client = useQuery({
    queryKey: ["auth-client", config.client_id],
    // Power BI's scope is named at sign-in, so its consent is asked together
    // with the API's and the dashboard needs no second trip to Microsoft.
    queryFn: () => createAuthClient(config, { extraScopes: powerBi ? [powerBi.scope] : [] }),
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: Number.POSITIVE_INFINITY,
    retry: false,
  });

  if (client.isPending) {
    return <Loading text={t("session.starting")} />;
  }
  if (client.isError) {
    return <ProblemPage kind="cannotStart" />;
  }

  const auth = client.data;
  if (auth.account === null) {
    return <SignedOut auth={auth} />;
  }
  return (
    <SignedIn auth={auth} account={auth.account} powerBi={powerBi}>
      {children}
    </SignedIn>
  );
}

function SignedOut({ auth }: { auth: AuthClient }) {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const afterSignOut = pathname === paths.signedOut;
  // Decided once, on arrival: the app goes to Microsoft unasked only when the
  // user neither signed out nor just came back from a sign-in that failed.
  const [automatic] = useState(
    () => !afterSignOut && auth.signInError === null && !signInWasStarted(),
  );

  useEffect(() => {
    // The stored note also stops a second run of this effect, which React
    // makes on purpose in development.
    if (automatic && !signInWasStarted()) {
      noteSignInStarted(true);
      void auth.signIn();
    }
  }, [automatic, auth]);

  if (automatic) {
    return <Loading text={t("session.redirecting")} />;
  }
  return (
    <SignInPage
      reason={afterSignOut ? "signedOut" : "failed"}
      errorCode={auth.signInError}
      onSignIn={() => {
        noteSignInStarted(true);
        void auth.signIn({ chooseAccount: true });
      }}
    />
  );
}

interface SignedInProps {
  auth: AuthClient;
  account: Account;
  powerBi: PowerBiConfig | null;
  children: ReactNode;
}

function SignedIn({ auth, account, powerBi, children }: SignedInProps) {
  const { t } = useTranslation();
  const api = useMemo(() => createApi(auth), [auth]);
  const me = useQuery({
    queryKey: ["me"],
    queryFn: ({ signal }) => fetchMe(api, signal),
    staleTime: ME_FRESH_FOR_MS,
    // A refusal does not change by asking again; the pages below say what to do.
    retry: false,
  });
  const session = useMemo(
    () => (me.data ? { auth, api, me: me.data, powerBi } : null),
    [auth, api, me.data, powerBi],
  );

  useEffect(() => {
    noteSignInStarted(false);
  }, []);

  if (session) {
    if (session.me.permissions.length === 0) {
      return (
        <NoAccessPage
          account={session.me.username ?? account.username}
          onSignOut={() => void auth.signOut()}
        />
      );
    }
    return <SessionContext value={session}>{children}</SessionContext>;
  }
  if (me.isPending || me.isFetching) {
    return <Loading text={t("session.starting")} />;
  }
  if (me.error instanceof ApiError && me.error.status === 401) {
    return (
      <ProblemPage
        kind="sessionRefused"
        onSignIn={() => void auth.signIn({ chooseAccount: true })}
      />
    );
  }
  return <ProblemPage kind="unreachable" onRetry={() => void me.refetch()} />;
}
