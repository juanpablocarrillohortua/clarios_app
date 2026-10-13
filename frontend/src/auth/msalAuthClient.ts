// Sign-in with Microsoft Entra ID, through MSAL. This is the only file that
// imports the library; everything else sees the AuthClient interface.
//
// The flow is "authorization code with PKCE", by redirect: the whole page goes
// to Microsoft and comes back. The app holds no secret.
import {
  type AccountInfo,
  InteractionRequiredAuthError,
  PublicClientApplication,
} from "@azure/msal-browser";

import type { AuthConfig } from "../api/config";
import { paths, REDIRECT_PAGE } from "../paths";
import type { AuthClient } from "./authClient";

/** The provider's code for an error, for the sign-in page and for support. */
function errorCode(error: unknown): string {
  if (typeof error === "object" && error !== null && "errorCode" in error) {
    const { errorCode: code } = error;
    if (typeof code === "string" && code) {
      return code;
    }
  }
  return "unknown_error";
}

export async function createMsalAuthClient(config: AuthConfig): Promise<AuthClient> {
  // Built from the address bar, so the same build works on :8080, on the dev
  // server and on a deployed host. Each resulting URI must be registered on
  // the app registration.
  const origin = window.location.origin;
  const msal = new PublicClientApplication({
    auth: {
      clientId: config.client_id,
      authority: config.authority,
      redirectUri: `${origin}${REDIRECT_PAGE}`,
      postLogoutRedirectUri: `${origin}${paths.signedOut}`,
    },
    // Tokens last as long as the tab. A new tab signs in again, without a
    // prompt while the user's Microsoft session is alive.
    cache: { cacheLocation: "sessionStorage" },
  });
  await msal.initialize();

  // Coming back from Microsoft, redirect.html has left the answer for this
  // call to pick up. It must finish before anything else is asked of MSAL.
  let account: AccountInfo | null = null;
  let signInError: string | null = null;
  try {
    account = (await msal.handleRedirectPromise())?.account ?? null;
  } catch (error) {
    signInError = errorCode(error);
  }
  account ??= msal.getActiveAccount() ?? msal.getAllAccounts()[0] ?? null;
  if (account) {
    msal.setActiveAccount(account);
  }

  // Microsoft returns from sign-out with a `state` parameter nothing reads.
  if (window.location.pathname === paths.signedOut && window.location.search) {
    window.history.replaceState(null, "", paths.signedOut);
  }

  // Asked for at sign-in, so consent and the first token come in one trip.
  const scopes = [config.api_scope];
  const signedIn = account;

  return {
    account: signedIn ? { name: signedIn.name ?? "", username: signedIn.username } : null,
    signInError,

    async signIn({ chooseAccount = false } = {}) {
      await msal.loginRedirect({ scopes, prompt: chooseAccount ? "select_account" : undefined });
    },

    async signOut() {
      await msal.logoutRedirect({ account: signedIn });
    },

    async getApiToken() {
      if (!signedIn) {
        throw new Error("a token was asked for while nobody is signed in");
      }
      try {
        // From the cache, or renewed without the user when it is about to expire.
        return (await msal.acquireTokenSilent({ scopes, account: signedIn })).accessToken;
      } catch (error) {
        if (error instanceof InteractionRequiredAuthError) {
          await msal.acquireTokenRedirect({ scopes, account: signedIn });
          // The page is leaving for Microsoft: there is no token to return.
          return new Promise<string>(() => {});
        }
        throw error;
      }
    },
  };
}
