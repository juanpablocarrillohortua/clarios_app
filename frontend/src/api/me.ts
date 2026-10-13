// Client for GET /api/me (src/energy/api/routers/me.py): who is signed in and
// what they may do. The app shows a section when `permissions` lists that
// section's permission. It never reads `roles` to decide anything: which role
// grants what is the API's rule (src/energy/api/security.py).
import type { Api } from "./client";

export interface Me {
  subject: string;
  name: string | null;
  username: string | null;
  roles: string[];
  permissions: string[];
  plant_scope: string[];
}

export function fetchMe(api: Api, signal?: AbortSignal): Promise<Me> {
  return api.get<Me>("/me", signal);
}
