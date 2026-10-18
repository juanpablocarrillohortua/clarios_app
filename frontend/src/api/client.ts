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
    /** The `details` of that envelope: what the refusal names, such as a run. */
    readonly details: Record<string, unknown> = {},
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

/** An answer whose status matters as much as its body: 201 or 200. */
export interface Sent<T> {
  status: number;
  body: T;
}

export interface Api {
  /** GET a path of the API, for example "/me", and return its JSON. */
  get<T>(path: string, signal?: AbortSignal): Promise<T>;
  /** POST to a path of the API, with no body, and return its JSON. */
  post<T>(path: string, signal?: AbortSignal): Promise<T>;
  /** POST JSON to a path of the API, and return the answer's status and JSON. */
  postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<Sent<T>>;
  /**
   * GET a file the API serves, such as a template or an original. When
   * `onProgress` is given, it hears the bytes received and the size the API
   * announced (0 when it announced none).
   */
  download(
    path: string,
    signal?: AbortSignal,
    onProgress?: (received: number, total: number) => void,
  ): Promise<Download>;
}

interface ErrorBody {
  code: string | null;
  details: Record<string, unknown>;
}

async function errorBody(response: Response): Promise<ErrorBody> {
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null && "code" in body) {
      const details =
        "details" in body && typeof body.details === "object" && body.details !== null
          ? (body.details as Record<string, unknown>)
          : {};
      return { code: typeof body.code === "string" ? body.code : null, details };
    }
  } catch {
    // Not JSON: a proxy's error page, for example.
  }
  return { code: null, details: {} };
}

export async function errorCode(response: Response): Promise<string | null> {
  return (await errorBody(response)).code;
}

// `attachment; filename="lineas_produccion.xlsx"`, or for a name beyond ASCII
// `attachment; filename="L_neas.xlsx"; filename*=UTF-8''L%C3%ADneas.xlsx`.
function filenameOf(disposition: string | null): string | null {
  const encoded = disposition?.match(/filename\*=UTF-8''([^;]+)/i);
  if (encoded?.[1]) {
    try {
      return decodeURIComponent(encoded[1]);
    } catch {
      // Not percent-encoding after all: fall back to the plain name.
    }
  }
  const match = disposition?.match(/filename="([^"]+)"/);
  return match?.[1] ?? null;
}

async function received(
  response: Response,
  onProgress: (received: number, total: number) => void,
): Promise<Blob> {
  const total = Number(response.headers.get("Content-Length") ?? 0);
  const reader = response.body?.getReader();
  if (!reader) return response.blob();
  const parts: BlobPart[] = [];
  let sum = 0;
  onProgress(0, total);
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parts.push(value);
    sum += value.byteLength;
    onProgress(sum, total);
  }
  return new Blob(parts, { type: response.headers.get("Content-Type") ?? "" });
}

export function createApi(auth: AuthClient): Api {
  async function call(
    path: string,
    method: "GET" | "POST",
    accept: string,
    signal?: AbortSignal,
    json?: unknown,
  ): Promise<Response> {
    const token = await auth.getApiToken();
    const headers: Record<string, string> = { Accept: accept, Authorization: `Bearer ${token}` };
    if (json !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(`/api${path}`, {
      method,
      signal,
      headers,
      body: json === undefined ? undefined : JSON.stringify(json),
    });
    if (!response.ok) {
      const { code, details } = await errorBody(response);
      throw new ApiError(response.status, code, details);
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
    async postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<Sent<T>> {
      const response = await call(path, "POST", "application/json", signal, body);
      return { status: response.status, body: (await response.json()) as T };
    },
    async download(
      path: string,
      signal?: AbortSignal,
      onProgress?: (received: number, total: number) => void,
    ): Promise<Download> {
      const response = await call(path, "GET", "*/*", signal);
      return {
        blob: onProgress ? await received(response, onProgress) : await response.blob(),
        filename: filenameOf(response.headers.get("Content-Disposition")),
      };
    },
  };
}
