// Keeps the access token of a report on screen from running out. A token lasts
// between 60 and 90 minutes, and a report is left open for longer.
//
// One request obtains a new token; three things ask for it:
//
//   1. a check every 30 seconds, which replaces the token when fewer than 10
//      minutes remain (the two values of Microsoft's sample);
//   2. the tab becoming visible again: a hidden tab's timers are slowed, and a
//      sleeping computer's stop;
//   3. Power BI itself, when its frame asks for a token or says that the one
//      it holds has expired.
//
// Nothing here knows about React or about the embedding library, so every
// path can be driven by a test with a clock it controls.
import type { ServiceToken } from "../auth/authClient";

export const CHECK_EVERY_MS = 30_000;
export const RENEW_BEFORE_MS = 10 * 60_000;

export interface TokenRenewalOptions {
  /** When the token now in the report expires. */
  expiresAt: Date;
  /** Obtain a new token. It rejects when none can be had. */
  renew: () => Promise<ServiceToken>;
  /** Hand a new token to the report on screen. */
  apply: (token: string) => Promise<void>;
  /** No new token could be had, and the one in the report has run out. */
  onExpired: (error: unknown) => void;
  /** A new token reached the report. */
  onRenewed?: (token: ServiceToken) => void;
  /**
   * Treat every token as lasting at most this long. Only the dev server sets
   * it, to see many renewals in a few minutes.
   */
  maxLifetimeMs?: number;
}

export interface TokenRenewal {
  /**
   * Power BI's frame asks for a token. Returns a new one, which the frame
   * installs itself, or null when none can be had. It never rejects.
   */
  tokenForFrame(): Promise<string | null>;
  /** Power BI says that the token has expired: replace it now. */
  replaceExpired(): Promise<void>;
  /** Stop checking. Nothing is handed to the report afterwards. */
  stop(): void;
}

export function keepTokenFresh(options: TokenRenewalOptions): TokenRenewal {
  const { renew, apply, onExpired, onRenewed, maxLifetimeMs } = options;

  function expiryOf(real: Date): number {
    return maxLifetimeMs === undefined
      ? real.getTime()
      : Math.min(real.getTime(), Date.now() + maxLifetimeMs);
  }

  let stopped = false;
  let expiresAt = expiryOf(options.expiresAt);
  let request: Promise<ServiceToken> | null = null;

  /** One request for a new token, however many triggers ask at the same time. */
  function newToken(): Promise<ServiceToken> {
    request ??= renew().finally(() => {
      request = null;
    });
    return request;
  }

  function renewed(token: ServiceToken): void {
    expiresAt = expiryOf(token.expiresAt);
    onRenewed?.(token);
  }

  async function replace(hasRunOut: () => boolean): Promise<void> {
    try {
      const token = await newToken();
      if (stopped) {
        return;
      }
      await apply(token.token);
      renewed(token);
    } catch (error) {
      // While the old token is still valid the report keeps working, and the
      // next check tries again.
      if (!stopped && hasRunOut()) {
        onExpired(error);
      }
    }
  }

  function check(): void {
    if (stopped || request !== null) {
      return;
    }
    if (expiresAt - Date.now() > RENEW_BEFORE_MS) {
      return;
    }
    void replace(() => Date.now() >= expiresAt);
  }

  function onVisibilityChange(): void {
    if (!document.hidden) {
      check();
    }
  }

  const timer = setInterval(check, CHECK_EVERY_MS);
  document.addEventListener("visibilitychange", onVisibilityChange);

  return {
    async tokenForFrame() {
      try {
        const token = await newToken();
        if (stopped) {
          return null;
        }
        renewed(token);
        return token.token;
      } catch {
        return null;
      }
    },

    replaceExpired: () => replace(() => true),

    stop() {
      stopped = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    },
  };
}
