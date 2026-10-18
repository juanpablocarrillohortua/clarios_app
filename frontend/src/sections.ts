// The sections of the app, in the order the navigation shows them. Each needs
// one permission, by the name the API uses for it. Whether a user holds that
// permission is the API's answer (GET /api/me), never worked out here.
import { paths } from "./paths";

export const sections = [
  { path: paths.dashboard, label: "nav.dashboard", permission: "dashboard:view" },
  { path: paths.loads, label: "nav.loads", permission: "loads:write" },
] as const;

export type Section = (typeof sections)[number];
export type Permission = Section["permission"];
