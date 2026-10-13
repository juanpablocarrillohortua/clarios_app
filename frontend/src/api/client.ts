// Calls to the API on behalf of the signed-in user. Every request carries the
// user's access token; the API decides what that user may do.
import type { AuthClient } from "../auth/authClient";

/** The API answered, with an error. */
export class ApiError extends Error {
  constructor(
    /** The HTTP status. */
    readonly status: number,
    /** The `code` of the API's error envelope, when the answer had one. */
    readonly code: string | null,
  ) {
    super(`the API answered ${status}${code ? ` (${code})` : ""}`);
    this.name = "ApiError";
  }
}

export interface Api {
  /** GET a path of the API, for example "/me", and return its JSON. */
  get<T>(path: string, signal?: AbortSignal): Promise<T>;
}

async function errorCode(response: Response): Promise<string | null> {
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null && "code" in body) {
      return typeof body.code === "string" ? body.code : null;
    }
  } catch {
    // Not JSON: a proxy's error page, for example.
  }
  return null;
}

export function createApi(auth: AuthClient): Api {
  return {
    async get<T>(path: string, signal?: AbortSignal): Promise<T> {
      const token = await auth.getApiToken();
      const response = await fetch(`/api${path}`, {
        signal,
        headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
      });
      if (!response.ok) {
        throw new ApiError(response.status, await errorCode(response));
      }
      return (await response.json()) as T;
    },
  };
}
