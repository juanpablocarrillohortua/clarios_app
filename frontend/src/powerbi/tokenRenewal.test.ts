// Keeping the token of a report on screen fresh, with a clock the test
// controls. This is the proof, without waiting an hour, of the second "done
// when" of increment 3: the report still works after its first token expires.
// That Power BI accepts the new token is seen only in a browser.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ServiceToken } from "../auth/authClient";
import {
  CHECK_EVERY_MS,
  keepTokenFresh,
  RENEW_BEFORE_MS,
  type TokenRenewal,
  type TokenRenewalOptions,
} from "./tokenRenewal";

const MINUTE = 60_000;
const LIFE = 60 * MINUTE;

/** The stand-ins for the sign-in client and for the report on screen. */
function setUp(overrides: Partial<TokenRenewalOptions> = {}) {
  let issued = 1;
  const renew = vi.fn(async (): Promise<ServiceToken> => {
    issued += 1;
    return { token: `token-${issued}`, expiresAt: new Date(Date.now() + LIFE) };
  });
  const apply = vi.fn(async (_token: string) => {});
  const onExpired = vi.fn();
  const onRenewed = vi.fn();
  const renewal = keepTokenFresh({
    // The first token, obtained just now.
    expiresAt: new Date(Date.now() + LIFE),
    renew,
    apply,
    onExpired,
    onRenewed,
    ...overrides,
  });
  started.push(renewal);
  return { renewal, renew, apply, onExpired, onRenewed };
}

const started: TokenRenewal[] = [];

function showTab(hidden: boolean): void {
  Object.defineProperty(document, "hidden", { configurable: true, value: hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers({ now: new Date("2026-10-13T12:00:00Z") });
});

afterEach(() => {
  for (const renewal of started.splice(0)) {
    renewal.stop();
  }
  vi.useRealTimers();
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
});

describe("the check every 30 seconds", () => {
  it("leaves a token alone while more than ten minutes remain", async () => {
    const { renew, apply } = setUp();

    await vi.advanceTimersByTimeAsync(LIFE - RENEW_BEFORE_MS - CHECK_EVERY_MS);

    expect(renew).not.toHaveBeenCalled();
    expect(apply).not.toHaveBeenCalled();
  });

  it("replaces the token once fewer than ten minutes remain", async () => {
    const { renew, apply, onRenewed, onExpired } = setUp();

    await vi.advanceTimersByTimeAsync(LIFE - RENEW_BEFORE_MS);

    expect(renew).toHaveBeenCalledTimes(1);
    expect(apply).toHaveBeenCalledExactlyOnceWith("token-2");
    expect(onRenewed).toHaveBeenCalledTimes(1);
    expect(onExpired).not.toHaveBeenCalled();
  });

  it("goes on for as long as the report is open: the second token is replaced too", async () => {
    const { apply } = setUp();

    // Past the life of the first token and of the one that replaced it: the
    // first is replaced at 50 minutes, and its replacement at 100.
    await vi.advanceTimersByTimeAsync(2 * LIFE);

    expect(apply.mock.calls.map(([token]) => token)).toEqual(["token-2", "token-3"]);
  });

  it("asks for one token at a time, however slow Microsoft is", async () => {
    let answer: (token: ServiceToken) => void = () => {};
    const renew = vi.fn(
      () =>
        new Promise<ServiceToken>((resolve) => {
          answer = resolve;
        }),
    );
    const { apply } = setUp({ renew });

    // Due, then four more checks while the first request is still out.
    await vi.advanceTimersByTimeAsync(LIFE - RENEW_BEFORE_MS + 4 * CHECK_EVERY_MS);
    expect(renew).toHaveBeenCalledTimes(1);

    answer({ token: "token-late", expiresAt: new Date(Date.now() + LIFE) });
    await vi.advanceTimersByTimeAsync(0);
    expect(apply).toHaveBeenCalledExactlyOnceWith("token-late");
  });
});

describe("a renewal that fails", () => {
  it("is tried again at the next check while the old token is still valid", async () => {
    const { renew, apply, onExpired } = setUp();
    renew.mockRejectedValueOnce(new Error("the network is down"));

    await vi.advanceTimersByTimeAsync(LIFE - RENEW_BEFORE_MS);
    expect(renew).toHaveBeenCalledTimes(1);
    expect(apply).not.toHaveBeenCalled();
    // The report still works: nothing is said.
    expect(onExpired).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(CHECK_EVERY_MS);
    expect(renew).toHaveBeenCalledTimes(2);
    expect(apply).toHaveBeenCalledExactlyOnceWith("token-2");
  });

  it("is reported once the token has run out, with the cause", async () => {
    const cause = new Error("Microsoft needs the user");
    const renew = vi.fn(async (): Promise<ServiceToken> => {
      throw cause;
    });
    const { onExpired } = setUp({ renew });

    await vi.advanceTimersByTimeAsync(LIFE - CHECK_EVERY_MS);
    expect(onExpired).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(CHECK_EVERY_MS);
    expect(onExpired).toHaveBeenCalledWith(cause);
  });

  it("counts a report that refuses the new token as a failed renewal", async () => {
    const { renew, apply, onRenewed } = setUp();
    apply.mockRejectedValueOnce(new Error("the frame did not take it"));

    await vi.advanceTimersByTimeAsync(LIFE - RENEW_BEFORE_MS);
    expect(onRenewed).not.toHaveBeenCalled();

    // The old token is still the one in the report, so the next check renews.
    await vi.advanceTimersByTimeAsync(CHECK_EVERY_MS);
    expect(renew).toHaveBeenCalledTimes(2);
    expect(onRenewed).toHaveBeenCalledTimes(1);
  });
});

describe("the tab becoming visible again", () => {
  it("replaces a token that came due while the computer slept", async () => {
    const { renew, apply } = setUp();
    showTab(true);
    // A sleeping computer runs no timer: the clock jumps, and no check ran.
    vi.setSystemTime(Date.now() + LIFE + 5 * MINUTE);

    showTab(false);
    await vi.advanceTimersByTimeAsync(0);

    expect(renew).toHaveBeenCalledTimes(1);
    expect(apply).toHaveBeenCalledExactlyOnceWith("token-2");
  });

  it("does nothing while the token has time left", async () => {
    const { renew } = setUp();

    showTab(true);
    showTab(false);
    await vi.advanceTimersByTimeAsync(0);

    expect(renew).not.toHaveBeenCalled();
  });

  it("does nothing when the tab is being hidden", async () => {
    const { renew } = setUp();
    vi.setSystemTime(Date.now() + LIFE);

    showTab(true);
    await vi.advanceTimersByTimeAsync(0);

    expect(renew).not.toHaveBeenCalled();
  });
});

describe("Power BI asking", () => {
  it("gets a new token for its frame, which installs it itself", async () => {
    const { renewal, renew, apply, onRenewed } = setUp();

    await expect(renewal.tokenForFrame()).resolves.toBe("token-2");

    expect(renew).toHaveBeenCalledTimes(1);
    expect(apply).not.toHaveBeenCalled();
    expect(onRenewed).toHaveBeenCalledTimes(1);
  });

  it("is told that there is none, never handed an error", async () => {
    const { renewal, renew } = setUp();
    renew.mockRejectedValueOnce(new Error("the network is down"));

    await expect(renewal.tokenForFrame()).resolves.toBeNull();
  });

  it("starts the ten minutes again from the token its frame was given", async () => {
    const { renewal, renew } = setUp();
    await vi.advanceTimersByTimeAsync(LIFE - RENEW_BEFORE_MS - MINUTE);
    await renewal.tokenForFrame();
    expect(renew).toHaveBeenCalledTimes(1);

    // Where the first token would have come due.
    await vi.advanceTimersByTimeAsync(2 * MINUTE);

    expect(renew).toHaveBeenCalledTimes(1);
  });

  it("has an expired token replaced at once, whatever the clock says", async () => {
    const { renewal, apply } = setUp();

    await renewal.replaceExpired();

    expect(apply).toHaveBeenCalledExactlyOnceWith("token-2");
  });

  it("is believed when it says the token expired and none can be had", async () => {
    const cause = new Error("Microsoft needs the user");
    const { renewal, renew, onExpired } = setUp();
    renew.mockRejectedValueOnce(cause);

    // By the clock the token has an hour left; Power BI's word wins.
    await renewal.replaceExpired();

    expect(onExpired).toHaveBeenCalledExactlyOnceWith(cause);
  });
});

describe("once the report is closed", () => {
  it("checks no more", async () => {
    const { renewal, renew } = setUp();

    renewal.stop();
    await vi.advanceTimersByTimeAsync(2 * LIFE);
    showTab(true);
    showTab(false);
    await vi.advanceTimersByTimeAsync(0);

    expect(renew).not.toHaveBeenCalled();
  });

  it("hands nothing to the report, even a token that was already on its way", async () => {
    let answer: (token: ServiceToken) => void = () => {};
    const renew = vi.fn(
      () =>
        new Promise<ServiceToken>((resolve) => {
          answer = resolve;
        }),
    );
    const { renewal, apply, onExpired } = setUp({ renew });
    await vi.advanceTimersByTimeAsync(LIFE - RENEW_BEFORE_MS);
    const forFrame = renewal.tokenForFrame();

    renewal.stop();
    answer({ token: "token-late", expiresAt: new Date(Date.now() + LIFE) });
    await vi.advanceTimersByTimeAsync(0);

    expect(apply).not.toHaveBeenCalled();
    expect(onExpired).not.toHaveBeenCalled();
    await expect(forFrame).resolves.toBeNull();
  });
});

describe("the dev server's switch", () => {
  it("treats every token as short-lived, so each check renews", async () => {
    const { apply } = setUp({ maxLifetimeMs: MINUTE });

    await vi.advanceTimersByTimeAsync(3 * CHECK_EVERY_MS);

    // Three checks in ninety seconds, three real renewals.
    expect(apply.mock.calls.map(([token]) => token)).toEqual(["token-2", "token-3", "token-4"]);
  });
});
