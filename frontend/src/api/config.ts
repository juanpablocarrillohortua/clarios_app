// Client for GET /api/config (src/energy/api/routers/client_config.py): what
// the app must know before anyone is signed in, and which Power BI report the
// dashboard shows. The values are identifiers, not secrets, and the server's
// .env is the only place they are written.

export interface AuthConfig {
  tenant_id: string;
  client_id: string;
  /** The address the browser is sent to for sign-in. */
  authority: string;
  /** The scope to ask an access token for; the API accepts no other. */
  api_scope: string;
}

/**
 * The Power BI report the dashboard shows. The addresses arrive whole, so the
 * app writes no address of Power BI's itself.
 */
export interface PowerBiConfig {
  workspace_id: string;
  report_id: string;
  /** The scope to ask a Power BI access token for. */
  scope: string;
  /** Where Power BI's REST API answers for the report. */
  report_api_url: string;
  /** The report in the Power BI Service, for a link a person follows. */
  report_web_url: string;
}

export interface ClientConfig {
  /** Null when sign-in is not configured on the server. */
  auth: AuthConfig | null;
  /** Null when no report is configured on the server. */
  powerbi: PowerBiConfig | null;
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
