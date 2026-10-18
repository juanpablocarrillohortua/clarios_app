# 4. A React single-page app with Entra ID sign-in and embedded Power BI

- **Status:** Accepted. Two libraries named in the decision were changed
  later. Point 2: the app uses `@azure/msal-browser` alone
  ([ADR 0006](0006-api-tokens-and-app-roles.md)). Point 3: it uses
  `powerbi-client` alone
  ([ADR 0008](0008-embedding-the-report-in-the-dashboard.md)). Point 1: there
  is no grid, and no form either; data is entered and corrected in Excel
  ([ADR 0009](0009-data-is-entered-and-corrected-in-excel.md), 2026-10-18)
- **Date:** 2026-10-12
- **Deciders:** Platform team

## Context

The platform needs a user interface with two sections: a dashboard that shows
the Power BI report, and a data-loading section where users add data that lands
in Bronze. [docs/frontend-plan.md](../frontend-plan.md) compares four stacks
against what this repository already has, and section 9 of that plan lists the
questions whose answers decide the design. They were answered on 2026-10-12.

The three hardest requirements are browser work:

- a grid that behaves like Excel, including pasting a block of cells;
- Power BI embedding that survives the expiry of its access token;
- uploads of up to 300 MB, with progress.

Plotly Dash, already in the stack, and Streamlit each meet them only by writing
JavaScript inside a Python framework or by accepting a weaker result (section 2
of the plan gives the details). The backend, in contrast, is already shaped for
a separate frontend: FastAPI is built, the upload contract is drafted in
`src/energy/api/routers/uploads.py`, and the Bronze loader is tested.

## Decision

**The frontend is a React single-page app (TypeScript, Vite) in `frontend/`,
served as static files by Caddy, on top of the FastAPI backend.** Every rule
stays in Python: the browser renders what the API tells it.

1. **Stack.** React, TypeScript and Vite; React Router; Mantine for the
   interface; TanStack Query for data fetching; i18next for the texts; Biome for
   formatting and linting; Vitest with Testing Library for tests; npm with a
   committed lockfile; Node 24 LTS in Docker and CI. The grid is chosen by a
   spike in increment 6, among MIT-licensed grids: there is no licence budget.

2. **Sign-in.** Microsoft Entra ID, in the project's dedicated tenant (single
   tenant), through the app registration "Clarios dash".
   - The app signs users in from the browser with the authorization code flow
     and PKCE, using `@azure/msal-react`. It holds no secret.
   - The API checks the token's signature, issuer and audience on every
     request, and maps Entra app roles onto the roles of
     `src/energy/api/security.py`.
   - MSAL Browser v5 needs a dedicated redirect page. It is `/redirect.html`,
     registered under the "Single-page application" platform.
   - `/auth/callback`, registered under the "Web" platform, belongs to a
     server-side flow that nothing uses today. Caddy keeps `/auth/*` out of the
     app. `ENTRA_CLIENT_SECRET` stays in `.env` and is passed to no container.

3. **Power BI.** The "embed for your organization" model: each user opens the
   report with their own Entra ID account, through `powerbi-client-react`. The
   report is published to the Power BI Service, which reads schema `reporting`
   through an on-premises data gateway on the machine that hosts the stack. In
   production, viewers have Power BI Pro.

4. **Hosting.** The same compose stack, on an internal server with a host name
   and HTTPS, which Caddy provides.

5. **Language.** Labels and messages are Spanish, kept in translation files.
   Code, comments and documentation stay in English.

6. **File size.** A file may be up to 300 MB. Data is loaded monthly, around the
   close. Section 6 of the plan sizes the upload, storage and validation for it.

Dash keeps its container for the Phase 5 energy charts and moves from `/` to
`/dash/`. Whether to keep it is decided when Phase 5 starts.

## Consequences

**Easier:**

- The three hard requirements use libraries made for them, two of them
  Microsoft's own.
- Hosting adds no server: the build is static files in the Caddy image.
- The server holds no Power BI secret, and access to the report follows the
  workspace permissions people already manage.
- A new dataset type needs no frontend code: the form and the grid are drawn
  from the dataset specification the API serves.

**Harder:**

- A second language and toolchain for a three-person Python team. `make check`
  and CI now need Node.
- Two dependency ecosystems to keep patched.
- Sign-in and embedding need HTTPS outside `localhost`, so a deployment needs a
  host name before anyone else can use it.

**Accepted:**

- The frontend is kept thin on purpose. A rule that appears only in TypeScript
  is a defect.
- Spanish texts are a deliberate exception to the repository's English-only
  rule, limited to the translation files.

## Risks

**The development tenant runs on a Microsoft Fabric free trial.** It lasts 60
days and started around 2026-10-12, so it ends around **2026-12-11**. The exact
date is shown in the Fabric portal.

- **What the trial gives.** A trial capacity and, for an account without a paid
  licence, a Power BI individual trial with the rights of a Premium Per User
  licence (Microsoft's documentation).
- **Where the report is.** In a workspace that is not on the trial capacity: on
  2026-10-12 the Power BI REST API reported it as not on a dedicated capacity.
  It is an ordinary Pro workspace, so the end of the trial capacity does not
  move it.
- **What happens then.** The accounts' trial licences end. In a Pro workspace
  the publisher and every viewer need a Power BI Pro licence, so an account
  without one can no longer publish, refresh or open the embedded report.
- **What it would break.** Increment 3 (the dashboard page) and increment 10
  (the dataset refresh), for every account without Pro.
- **Mitigation.**
  - Before the date, give the publisher and the test users Pro licences, or
    assign the workspace to a paid capacity.
  - Keep only a semantic model and a report in the workspace, nothing that
    exists only in Fabric.
  - Keep the `.pbix` file, so the report can be published again.
- **Not verified.** Which licence each account holds today was not read, and
  that the individual trials end on the same day as the trial capacity is
  inferred from Microsoft's page, which presents them as one trial. Check both
  in the tenant before the date.

## Alternatives considered

**Stay on Plotly Dash.** Rejected: pasting a block from Excel needs a paid AG
Grid Enterprise licence, `dcc.Upload` holds the file in browser memory, and
there is no official Power BI component. It remains workable with those limits
accepted, and the backend design would be the same.

**Streamlit.** Rejected: its built-in sign-in cannot request the Power BI
permission, the whole script reruns on each interaction, and there is little
control over errors on single cells.

**Next.js.** Rejected: the app sits behind Caddy and talks to FastAPI, so
server-side rendering buys nothing and costs a Node server to run.

**"Embed for your customers".** Rejected for now: it needs a paid capacity in
production and a service principal whose secret lives on the server. It becomes
the choice if viewers turn out to have no Power BI licence.

**Sign-in on the server** (the "Web" platform, with the client secret and a
session cookie). Rejected: the browser needs the user's own Power BI token in
any case, and MSAL obtains and renews it there without a secret.

## References

- [docs/frontend-plan.md](../frontend-plan.md) — the comparison, the design and
  the increments.
- [ADR 0003](0003-modular-monolith-over-microservices.md) — the entry points the
  frontend talks to.
- [ADR 0005](0005-api-composition-root.md) — where the API binds adapters.
- [docs/powerbi.md](../powerbi.md) — the gateway, its connection and what a
  refresh of the published report needs.
- MSAL Browser, the redirect bridge:
  <https://learn.microsoft.com/en-us/entra/msal/javascript/browser/redirect-bridge>
- Redirect URI restrictions:
  <https://learn.microsoft.com/en-us/entra/identity-platform/reply-url>
- Microsoft Fabric trial:
  <https://learn.microsoft.com/en-us/fabric/fundamentals/fabric-trial>
