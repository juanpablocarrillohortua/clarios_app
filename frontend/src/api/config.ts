// Client for GET /api/config (src/energy/api/routers/client_config.py): what
// the app must know before anyone is signed in. The values are identifiers,
// not secrets, and the server's .env is the only place they are written.

export interface AuthConfig {
  tenant_id: string;
  client_id: string;
  /** The address the browser is sent to for sign-in. */
  authority: string;
  /** The scope to ask an access token for; the API accepts no other. */
  api_scope: string;
}

export interface ClientConfig {
  /** Null when sign-in is not configured on the server. */
  auth: AuthConfig | null;
}

export async function fetchClientConfig(signal?: AbortSignal): Promise<ClientConfig> {
  const response = await fetch("/api/config", {
    signal,
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`the API answered ${response.status} to the request for its settings`);
  }
  return (await response.json()) as ClientConfig;
}
