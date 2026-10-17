# 8. The dashboard embeds the report with the user's own Power BI token

- **Status:** Accepted
- **Date:** 2026-10-13
- **Amended:** 2026-10-16 (decision 10)
- **Deciders:** Platform team

## Context

[ADR 0004](0004-react-spa-entra-and-embedded-power-bi.md) decided that the web
app shows the Power BI report in the model Microsoft calls "embed for your
organization": each user opens the report with their own Entra ID account, and
the server holds no Power BI secret.
[ADR 0007](0007-figures-reach-people-through-power-bi.md) made that report the
only place where a person sees a figure.

Increment 3 of [docs/frontend-plan.md](../frontend-plan.md) builds the page.
ADR 0004 left open what the page needs in order to do it:

- **which permission** the app asks of Power BI, and how the user consents;
- **where the app learns** which report to show;
- **how the report's address is obtained**, and how a refusal is told apart
  from a failure;
- **how the report outlives its token**, which lasts between 60 and 90
  minutes;
- **where the "data as of" line comes from**, which ADR 0007 left to this
  increment.

ADR 0004 also named the library `powerbi-client-react`.

Section 10 of the plan lists what was read from Microsoft's documentation and
from the Power BI REST API before deciding.

## Decision

1. **One delegated permission: `Report.Read.All` of the Power BI Service.** It
   lets the app read reports the user may already open. The app asks for no
   permission to write, to refresh or to read models.

2. **An administrator grants consent for the tenant, once.** The permission
   allows users to consent themselves; admin consent means nobody is asked. The
   app also names the scope at sign-in, so that in a tenant without admin
   consent the user sees one consent screen and not two.

3. **The Power BI token comes from the same sign-in,** silently, through two
   new operations of `AuthClient`: one returns a token for a service and the
   time it expires, the other sends the browser to Microsoft. **The first never
   redirects.** When Microsoft needs the user, the page shows a message and a
   button. A refusal therefore cannot become a loop.

4. **The app learns which report to show from the API.** Two variables,
   `POWERBI_WORKSPACE_ID` and `POWERBI_REPORT_ID`, are written in `.env` and
   returned by `GET /config` beside the sign-in settings, with the scope and
   the two Power BI addresses built from them. With either one empty, the
   dashboard says that the report is not configured, and the rest of the app
   works. Nothing depends on the name of the report.

5. **The browser asks Power BI for the report before embedding it,** with one
   `GET` to Power BI's REST API. The answer carries the embed address, which
   holds more than the two IDs. A refusal arrives as an HTTP status and a
   header, which the app turns into one message per cause. **`GET` is the only
   method the app sends to Power BI.**

6. **The app uses `powerbi-client` alone,** behind an interface of its own
   (embed a report, replace its token, remove it). This replaces
   `powerbi-client-react` in point 3 of ADR 0004. One file imports the library,
   and it is loaded when the dashboard opens, not with the app.

7. **The page keeps the token fresh.** It checks every 30 seconds and when the
   tab becomes visible, and replaces the token when fewer than 10 minutes
   remain. It also answers Power BI when the frame asks for a token. Every path
   ends in the library's `setAccessToken`.

8. **The "data as of" line belongs to the report,** as a card the refresh
   itself feeds. The app does not draw it.

9. **No Content-Security-Policy is added with this increment.** The Caddyfile
   is unchanged. A policy is part of the hardening increment.

10. **The whole page is in view at once, in a frame of the page's shape,
    and Power BI shows none of its own panes** (2026-10-16).
    - **The page is fitted to the page,** never scrolled: the owner wants the
      whole dashboard at a glance.
    - **The frame takes the open page's shape.** Power BI tells the app the
      page's size once the report has loaded and on each change of page, and
      the frame becomes the largest box of that shape the area holds,
      centred. The page then fills its frame, and what a wide window has left
      beside it is the app's own background, not black bands inside the
      frame. Until Power BI has said, the frame fills the area.
    - **A "Pantalla completa" button** (wide screens, where the browser
      supports it) puts the area in full screen. On a 16:9 monitor the page
      then fills the screen.
    - **Power BI's page bar and filter pane are hidden.** This replaces "page
      navigation visible, filter pane available and collapsed" of section 10
      of the plan.

    It follows from the report's own design, read from its definition through
    the Power BI REST API on 2026-10-16:
    - all eight pages are 1280 × 720;
    - every page has a button to each page and its slicers on the page;
    - the one filter of each page's pane is hidden from viewers.

    On a wide screen the app's chrome also moved into its header, so the
    report has the rest of the window.

## Consequences

**Easier:**

- The server still holds no Power BI secret and makes no call to Power BI.
- Who sees the report is decided in one place, the workspace, by people who
  already manage it.
- Each refusal has its own message, taken from a status code and not from the
  text of an error.
- The page and every other test use a stand-in for the embed interface. One
  test file stubs Microsoft's library.
- A user who only loads data never downloads the embedding library.

**Harder:**

- **A viewer needs three things, given separately:** the app role, access to
  the workspace, and a Power BI licence.
- The app registration carries one more permission, set by hand.
  [docs/sign-in.md](../sign-in.md) lists it.
- The browser now talks to three services: the API, Entra ID and Power BI. A
  network that blocks `api.powerbi.com` or `app.powerbi.com` shows as "Power
  BI cannot be reached".
- The embedding library has no ES module build, and adds to what the browser
  downloads on the dashboard.
- **The report must carry its own navigation and filters** (decision 10).
  A page without buttons to the other pages cannot be left inside the app,
  and a filter made visible in the filter pane is not seen there. Both are
  rules for whoever edits the report; [docs/powerbi.md](../powerbi.md)
  states them.
- **A desktop window is wider than the 16:9 page,** once the browser's bars
  take their height. The page is therefore as tall as the window allows, and
  the app's background shows on both sides: about 175 pixels each on a 1080p
  screen. Full screen removes them on a 16:9 monitor.

**Accepted:**

- **`GET /config` is public and names the workspace and the report.** They are
  identifiers: they open nothing without the token of a user who has access.
- **A wrong workspace ID and a user without access look the same.** Power BI
  answers both with 401 and `GroupNotAccessible`.
- **How Power BI refuses a user without a licence was not known when this was
  written.** That user gets a general message naming both access and licence,
  until the answer has been seen.
- **A user who was just given access may be refused until they open the Power
  BI Service once.** Microsoft's remedy is a `POST`, which the app does not
  send; the message says what to do.
- **The sign-in ends after 24 hours** in a single-page app. A report left open
  longer shows the message that asks the user to continue with Microsoft.

## Alternatives considered

**`powerbi-client-react`.** Rejected: it is one component around
`powerbi-client`. The app needs four operations, and wrapping them itself keeps
the library in one file, as ADR 0006 did for MSAL.

**Assemble the embed address from the two IDs.** Rejected: the address Power BI
returns carries a `config` parameter of its own, and without the `GET` a
refusal would have to be read from the frame's error events.

**Leave token renewal to the library's `accessTokenProvider` hook alone.**
Rejected as the only path: when the frame calls it is Power BI's decision, so
it cannot be driven in a test or shown in a browser without waiting for a real
expiry. It is kept as a second path.

**Send the browser to Microsoft automatically when the Power BI token needs the
user.** Rejected: a consent that cannot be given would bring the user back to
the same redirect.

**Read the time of the last refresh from Power BI's REST API.** Rejected: it
needs Write permission on the model, which a viewer does not have, and a second
permission for the app.

**Serve the two IDs from `GET /me`,** to signed-in callers only. Rejected for
now: one endpoint already serves what the app needs before it draws anything,
and the IDs are not secrets.

**Add a Content-Security-Policy now.** Rejected: a wrong one breaks sign-in or
the report, and only a signed-in browser shows it.

**Fit the page to the frame's width** (decision 10). Tried on 2026-10-16 and
withdrawn the same day: the page filled the width with no bands, but about 200
pixels of it had to be scrolled inside the frame, and the owner wants the
whole dashboard at a glance.

**Write the page's shape into the app** (decision 10). Rejected: the frame's
shape would silently go wrong the day the report's page size changes. Power
BI tells it.

**Change the report's page size to the app's area** (decision 10). Rejected:
the area's proportions change with every window, so no one size fits. It
would also mean laying out eight pages again, and publishing from Desktop
again drops the gateway's mapping.

## References

- [ADR 0004](0004-react-spa-entra-and-embedded-power-bi.md) — the embedding
  model. Its point 3 is amended here.
- [ADR 0006](0006-api-tokens-and-app-roles.md) — `AuthClient`, and where the
  app gets its settings.
- [ADR 0007](0007-figures-reach-people-through-power-bi.md) — the report as the
  one source of figures.
- [docs/frontend-plan.md](../frontend-plan.md), section 10 — the plan of the
  increment, what was checked, and the manual checks.
- [docs/powerbi.md](../powerbi.md) — the report inside the web app: what a
  viewer needs, and troubleshooting.
- Embedding for your organization:
  <https://learn.microsoft.com/en-us/power-bi/developer/embedded/embed-organization-app>
- Refreshing the access token:
  <https://learn.microsoft.com/en-us/javascript/api/overview/powerbi/refresh-token>
- Errors of an embedded report:
  <https://learn.microsoft.com/en-us/power-bi/developer/embedded/embedded-troubleshoot>
- Get Refresh History In Group:
  <https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-history-in-group>
