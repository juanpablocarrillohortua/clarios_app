// Client for GET /api/health (src/energy/api/routers/health.py).

export interface Health {
  status: string;
  service: string;
  version: string;
  environment: string;
}

export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const response = await fetch("/api/health", {
    signal,
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`the API answered ${response.status} to the health check`);
  }
  return (await response.json()) as Health;
}
