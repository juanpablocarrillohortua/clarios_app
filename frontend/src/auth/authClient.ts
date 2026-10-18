// The app's own view of sign-in. Pages and the API helper depend on this
// interface and never on the sign-in library, so a test can hand the app a
// stand-in, and the library is named in one file only (msalAuthClient.ts).
import type { AuthConfig } from "../api/config";

/** Who is signed in. For display only: nothing is decided from these. */
export interface Account {
  /** The person's name; empty when the identity provider gave none. */
  name: string;
  /** The name the person signs in with. */
  username: string;
}

/** An access token for a service, and when that service stops accepting it. */
export interface ServiceToken {
  token: string;
  expiresAt: Date;
}

/**
 * The identity provider cannot give a token without the user: consent is
 * missing, a second factor is asked for, or the session ended.
 */
export class InteractionNeeded extends Error {
  constructor(
    /** The provider's code for why, for example "consent_required". */
    readonly code: string,
  ) {
    super(`the identity provider needs the user (${code})`);
    this.name = "InteractionNeeded";
  }
}

export interface AuthClient {
  /** Who is signed in in this browser tab, or null. */
  readonly account: Account | null;
  /**
   * Why the last return from the provider's pages failed, as the provider's
   * error code; null when it did not fail.
   */
  readonly signInError: string | null;
  /**
   * Send the browser to the sign-in page. The page unloads, so on success the
   * promise never settles.
   */
  signIn(options?: { chooseAccount?: boolean }): Promise<void>;
  /** Sign out here and at the identity provider. The page unloads. */
  signOut(): Promise<void>;
  /**
   * An access token for the API, renewed when it is about to expire. When it
   * cannot be renewed without the user, the browser is sent to sign in again.
   */
  getApiToken(): Promise<string>;
  /**
   * An access token for another service the user may use, such as Power BI,
   * from the same sign-in. With `fresh`, a new token is asked for even while
   * the one held is still valid.
   *
   * It never sends the browser away. When the provider needs the user it
   * rejects with InteractionNeeded, and the page offers authorizeService().
   */
  getServiceToken(scope: string, options?: { fresh?: boolean }): Promise<ServiceToken>;
  /**
   * Send the browser to the provider, to give consent for that service or to
   * confirm the session. The page unloads.
   */
  authorizeService(scope: string): Promise<void>;
}

export interface AuthClientOptions {
  /**
   * Scopes of other services to ask consent for at sign-in, so that a consent
   * screen, when there is one, is shown once and not once per service.
   */
  extraScopes?: readonly string[];
}

/** Builds the client once the app knows the sign-in settings. */
export type CreateAuthClient = (
  config: AuthConfig,
  options?: AuthClientOptions,
) => Promise<AuthClient>;
