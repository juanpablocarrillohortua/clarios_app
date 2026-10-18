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

/** A file the API sent, to be saved by the user. */
export interface Download {
  blob: Blob;
  /** The name the API gave the file, when it gave one. */
  filename: string | null;
}

export interface Api {
  /** GET a path of the API, for example "/me", and return its JSON. */
  get<T>(path: string, signal?: AbortSignal): Promise<T>;
  /** POST to a path of the API, with no body, and return its JSON. */
  post<T>(path: string, signal?: AbortSignal): Promise<T>;
  /** GET a file the API serves, such as a template. */
  download(path: string, signal?: AbortSignal): Promise<Download>;
}

export async function errorCode(response: Response): Promise<string | null> {
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

// `attachment; filename="lineas_produccion.xlsx"`
function filenameOf(disposition: string | null): string | null {
  const match = disposition?.match(/filename="([^"]+)"/);
  return match?.[1] ?? null;
}

export function createApi(auth: AuthClient): Api {
  async function call(
    path: string,
    method: "GET" | "POST",
    accept: string,
    signal?: AbortSignal,
  ): Promise<Response> {
    const token = await auth.getApiToken();
    const response = await fetch(`/api${path}`, {
      method,
      signal,
      headers: { Accept: accept, Authorization: `Bearer ${token}` },
    });
    if (!response.ok) {
      throw new ApiError(response.status, await errorCode(response));
    }
    return response;
  }

  return {
    async get<T>(path: string, signal?: AbortSignal): Promise<T> {
      const response = await call(path, "GET", "application/json", signal);
      return (await response.json()) as T;
    },
    async post<T>(path: string, signal?: AbortSignal): Promise<T> {
      const response = await call(path, "POST", "application/json", signal);
      return (await response.json()) as T;
    },
    async download(path: string, signal?: AbortSignal): Promise<Download> {
      const response = await call(path, "GET", "*/*", signal);
      return {
        blob: await response.blob(),
        filename: filenameOf(response.headers.get("Content-Disposition")),
      };
    },
  };
}
