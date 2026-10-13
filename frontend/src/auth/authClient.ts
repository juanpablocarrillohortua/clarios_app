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

export interface AuthClient {
  /** Who is signed in in this browser tab, or null. */
  readonly account: Account | null;
  /**
   * Why the return from the sign-in page failed, as the provider's error
   * code; null when it did not fail.
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
}

/** Builds the client once the app knows the sign-in settings. */
export type CreateAuthClient = (config: AuthConfig) => Promise<AuthClient>;
