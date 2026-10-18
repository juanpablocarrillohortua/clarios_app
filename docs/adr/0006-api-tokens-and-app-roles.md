# 6. The API accepts Entra ID access tokens and reads app roles

- **Status:** Accepted
- **Date:** 2026-10-12
- **Deciders:** Platform team

## Context

[ADR 0004](0004-react-spa-entra-and-embedded-power-bi.md) decided that users
sign in to the web app with Microsoft Entra ID, from the browser, and that the
API checks a token on every request and maps Entra app roles onto the roles of
`src/energy/api/security.py`. It left four things open, which increment 2 of
[docs/frontend-plan.md](../frontend-plan.md) had to settle before any code:

- **which token** the browser sends to the API;
- **how a role becomes a decision**, in the API and in the browser, without the
  same rule being written twice;
- **where the app gets** the identifiers of the tenant and the app registration;
- **what happens** to a signed-in user who was given no role.

ADR 0004 also named the library `@azure/msal-react`. Building the increment
showed a simpler option.

## Decision

1. **One app registration is both the app and the API.** It exposes one scope,
   `access_as_user`. The app asks Entra ID for an **access token** for that
   scope and sends it as `Authorization: Bearer`. The ID token never goes to the
   API: it says who signed in to the app, not that the API may be called.

2. **Version 2 access tokens only.** The registration's manifest asks for them
   (`requestedAccessTokenVersion: 2`). The API accepts a token when all of this
   holds:
   - it is signed with RS256 by a key the tenant publishes;
   - it has not expired, with a minute of tolerance between clocks;
   - its issuer and tenant are this tenant, and its version is 2.0;
   - its audience is this app registration;
   - it carries the scope `access_as_user`, so a person is signed in;
   - it names that person.

3. **Roles are Entra app roles:** `viewer`, `operator`, `engineer`, `admin`,
   assigned to users in Entra ID. They arrive in the token's `roles` claim. A
   value the application does not define grants nothing. **There is no default
   role:** a user without one is signed in and may do nothing.

4. **Endpoints ask for a permission, never for a role.** One table in
   `api/security.py` says which roles grant each permission. `GET /me` returns
   the caller's permissions, and the app shows a section when its permission is
   listed. The browser holds no rule about roles.

5. **A route is protected unless it is listed as public.** The policy is in
   `api/main.py`, where the routers are mounted. A test calls every operation
   the API publishes without a token and fails for one that answers.

6. **The app reads its sign-in settings from the API**, at `GET /config`, which
   reads them from `.env`. They are not built into the app.

7. **The app uses `@azure/msal-browser` alone**, behind an interface of its own
   (`AuthClient`: the account, sign in, sign out, a token). This replaces
   `@azure/msal-react` in point 2 of ADR 0004. One file imports MSAL.

8. **Sign-in is by redirect:** the whole page goes to Microsoft and returns. It
   starts by itself on a first visit, and never twice in a row: after a failed
   or cancelled sign-in the user is asked. Sign-out also ends the session at
   Microsoft.

9. **Tokens are kept in the tab's session storage**, MSAL's default.

10. **In the hexagon:** the port is `ports/token_verifier.py`, the adapter is
    `adapters/entra_token_verifier.py`, and `api/deps.py` binds them. That is
    the first use of the exception [ADR 0005](0005-api-composition-root.md)
    allowed.

## Consequences

**Easier:**

- The API needs no secret. It checks tokens with Entra ID's public keys.
- Access is managed where accounts already are: assigning a role in Entra ID is
  all it takes, and removing it is all it takes to revoke.
- The identifiers of the tenant and the app registration are written in one
  place, `.env`. They are in neither the repository nor the image, so one image
  runs in every environment.
- A change to what a role may do is one line of Python and its test. The app
  follows without a change.
- The app is tested by handing it a stand-in for `AuthClient`. No test stubs
  Microsoft's library except the tests of the one file that uses it.

**Harder:**

- The registration must be set up by hand: two redirect URIs, the scope, the
  token version, the four roles. [docs/sign-in.md](../sign-in.md) lists them.
  A wrong value shows only when someone signs in.
- The API container must reach `login.microsoftonline.com` to fetch the signing
  keys. It keeps them for a day and survives a short outage; it cannot start
  checking tokens without a first answer.
- The app makes one request before it can sign anyone in.

**Accepted:**

- **A role change is not immediate.** It takes effect with the user's next
  token: after signing out and in, or within about 90 minutes.
- **A token in session storage can be read by a script running in the page.**
  The app loads no third-party script. A content security policy is part of the
  hardening increment.
- **A new browser tab signs in again.** While the user's Microsoft session is
  alive this needs no prompt.
- **`/dash/`, `/prefect/` and `/minio/` are not covered.** Caddy still serves
  them without the app's sign-in. That is acceptable on `localhost` and must be
  closed before the stack runs on a shared host.

## Alternatives considered

**Send the ID token to the API.** Rejected: its audience is the app, it carries
no scope, and Microsoft's guidance is that an API accepts only access tokens
issued for it. It would work until it mattered.

**Sign in on the server**, with the client secret and a session cookie.
Rejected in ADR 0004, and again here: the browser needs the user's own Power BI
token in any case, and a cookie session adds a secret, a session store and
protection against cross-site requests.

**Entra groups instead of app roles.** Rejected: group identifiers differ in
every tenant, so the code would name identifiers of one directory. App roles
belong to the registration and carry the names the code uses.

**A default role for every account of the tenant.** Rejected: access would
depend on who exists in the directory. An explicit assignment is one step and
leaves a record.

**Settings built into the app** (`VITE_*` variables at build time). Rejected:
the identifiers would be in the image, and each environment would need its own
build.

**`@azure/msal-react`.** Rejected: the app needs four operations and a redirect
flow, in which the page reloads on every change of account. The hooks add a
second library and make every test stub Microsoft's provider.

**Accept version 1 tokens as well.** Rejected: two issuers, two audiences and
two sets of claim names to check and test, to save one manifest setting.

**Sign in through a pop-up window.** Rejected: browsers block pop-ups that a
click did not open, and the first visit must sign in without one.

## References

- [ADR 0004](0004-react-spa-entra-and-embedded-power-bi.md) — sign-in with
  Entra ID from the browser. Its point 2 is amended here.
- [ADR 0005](0005-api-composition-root.md) — why `api/deps.py` may import the
  adapter.
- [docs/sign-in.md](../sign-in.md) — the registration, giving access, and
  troubleshooting.
- `src/energy/api/security.py` — the roles, the permissions and the table.
- `tests/unit/test_api_auth.py`, `tests/unit/test_entra_token_verifier.py` —
  the rules above, as tests.
- Access tokens and how an API validates them:
  <https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens>
- App roles:
  <https://learn.microsoft.com/en-us/entra/identity-platform/howto-add-app-roles-in-apps>
- MSAL Browser 5, the redirect page:
  <https://learn.microsoft.com/en-us/entra/msal/javascript/browser/redirect-bridge>
