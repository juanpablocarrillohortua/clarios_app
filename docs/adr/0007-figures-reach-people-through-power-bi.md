# 7. Figures reach people through Power BI, and the API serves none

- **Status:** Accepted. The code changes it calls for wait for the start of
  Phase 4 ("What Phase 4 does first", below)
- **Date:** 2026-10-13
- **Deciders:** Platform team

## Context

The Phase 1 scaffold gave the API three placeholder endpoints for KPI figures,
in `src/energy/api/routers/kpis.py`:

- `/kpis`, one KPI as a time series;
- `/kpis/summary`, the latest value of every KPI;
- `/kpis/{plant_code}/coverage`, how much of a period rests on reliable
  readings.

Their docstring says they serve "dashboards, reports and external consumers".
No consumer was ever named. Dash was designed to read through `KPIService`
inside its own process ([docs/architecture.md](../architecture.md), "A
dashboard query"), so it never needed them.

[ADR 0004](0004-react-spa-entra-and-embedded-power-bi.md) then decided how
people see figures: in a Power BI report embedded in the web app. The report
reads the views of schema `reporting` through the bridge and the gateway
([docs/powerbi.md](../powerbi.md)). Its data does not pass through the API.

**The two do not overlap today.** The endpoints are for the energy KPIs of
`config/kpis.yaml` (energy per unit, load factor, deviation from the baseline),
none of which is computed yet. The report shows the equivalent batteries. They
would overlap from Phase 4 on, when the energy KPIs reach Gold and have to be
shown somewhere.

Serving one figure from two places has two costs:

- **Two levels of freshness.** The API would read Gold as it is now. The
  report holds a copy, as of its last refresh. A user could see two values for
  the same figure.
- **Two access models.** The API decides by the role in the user's token. Power
  BI decides by access to its workspace. A user could be allowed one and
  refused the other.

**What the web app would use.** No page of
[docs/frontend-plan.md](../frontend-plan.md) calls a KPI endpoint. Its API
routers are `datasets`, `drafts`, `me`, `config`, `uploads` and `runs`.

| Endpoint | What could use it in the app | Can the embedded report cover it? |
|---|---|---|
| `/kpis` | A chart the app draws itself | Yes: a series by period is what a report visual is |
| `/kpis/summary` | Tiles the app draws itself | Yes: a card with a change indicator is a standard visual |
| `/kpis/{plant_code}/coverage` | A footnote on a figure | Yes, when each row of the reporting view carries its `coverage`, as every Gold row must |
| | Feedback right after a load | **No.** The report is a copy until its next refresh |

One need survives: seeing what a load did before Power BI is refreshed. It is a
question about a load, not about a plant and a period, and the loading wizard
holds a run ID when it asks. It also concerns energy readings only: coverage
has no meaning for the EQUS exports, and energy readings are outside the web
app's scope for now (question 11 of the plan).

## Decision

1. **Figures reach people through one path:** Gold, then the views of schema
   `reporting`, then Power BI, embedded in the web app. The API serves no
   analytical figure.

2. **The three KPI endpoints are removed, not implemented.** The removal is the
   first step of Phase 4. Until then they stay as they are, placeholders that
   answer 501, and nobody builds on them.

3. **Phase 4 delivers the energy KPIs to Power BI.** It adds `reporting` views
   over the Gold KPI tables, and each row carries its `coverage`.

4. **Feedback after a load belongs to the run.** The contract of
   `/runs/{run_id}` already holds the period a run covered and its row counts,
   quarantined rows included (`RunDetailResponse`); the endpoint itself is a
   Phase 3 placeholder. When Phase 4 builds coverage accounting, the coverage
   of that period joins the run's detail.

5. **`dashboard:view` stays, as navigation.** It makes the app show the
   dashboard section. It does not decide who sees a figure: Power BI does, by
   access to the workspace.

6. **An endpoint may serve a figure again only with a new decision** that
   names the page that needs it, shows that a refresh cannot deliver it in
   time, and reads the same reporting view the report reads. Then only the
   freshness can differ, never the definition.

## Consequences

**Easier:**

- A figure has one definition and one freshness wherever a person sees it.
  "The report says one thing and the app another" cannot happen.
- Access to figures has one model, Power BI's.
- Phase 4 is smaller: no endpoints, no response models, none of their tests.
  It writes views.
- The reporting views already carry the rules that keep figures consistent:
  they restate Gold, they never call a Gold function, and each grants itself to
  the reader role.

**Harder:**

- **A figure is as fresh as the last refresh, and refreshes are limited.** The
  model is in Import mode. Microsoft allows a model on shared capacity eight
  refreshes a day, counting the scheduled ones and the ones started through
  the REST API together; a refresh started by hand in the Service is not
  counted. The project's workspace is on shared capacity
  ([docs/powerbi.md](../powerbi.md)), and viewers with Power BI Pro keep it
  there. A refresh also needs the stack, the bridge and the gateway running.
- **The same limit bounds increment 10 of the frontend plan,** which asks
  Power BI to refresh after a load: eight a day, with any scheduled refresh
  taken from the same eight.
- **Every new figure is three steps:** a view with its grant, a change to the
  model in Power BI Desktop, and publishing again, which drops the mapping to
  the gateway each time.
- **`dashboard:view` protects no route of the API** once the endpoints are
  gone. The tests that use `/kpis/summary` as their example of a guarded route
  need another.

**Accepted:**

- **Energy figures may be hours old.** That suits monthly and daily reading.
  It does not suit watching the current hour.
- **No figure without Power BI.** A user without a licence, or without access
  to the workspace, sees none. ADR 0004 already carries that risk.
- **A viewer needs two grants:** the app role, and access to the workspace.
  They are given and removed separately, by hand. One group for both would
  keep them in step, but assigning a group to an app role needs Microsoft
  Entra ID P1 or P2.

## What would reopen this

A page of the web app that needs a figure sooner than a refresh can deliver.
The likely case is energy at hourly grain, if the plant wants it within
minutes. Two answers exist then, and point 6 applies to both:

- **Power BI in DirectQuery.** Every visual queries the database through the
  gateway. The reporting login is sized for imports: 10 connections and a
  10-minute statement timeout.
- **An endpoint over the same reporting view,** drawn by the app.

## What Phase 4 does first

Nothing below is built before Phase 4 starts.

1. Ask the plant how fresh energy figures must be. If the answer is "within
   minutes", reopen this decision before building anything.
2. Remove `src/energy/api/routers/kpis.py`, its mount and its tag in
   `api/main.py`, and its name in `api/routers/__init__.py`.
3. Remove the response models only that router uses (`KPIPoint`,
   `KPISeriesResponse`, `KPISummaryItem`, `KPISummaryResponse`) and
   `KPIQueryRequest`, which nothing uses.
4. Give the tests that call `/kpis/summary` another guarded route:
   `tests/unit/test_api_auth.py` and
   `tests/integration/test_health_endpoint.py`.
5. Remove the three rows from the endpoint table of
   [docs/api.md](../api.md), and "the KPI query endpoints" from the roadmap of
   `README.md`. The README waits because an edit to it makes the next image
   build reinstall every Python dependency.
6. Build the `reporting` views of the energy KPIs, with `coverage`, each with
   its own `GRANT SELECT` to `reporting_reader`.
7. Add the coverage of a run's period to the detail of a run.

`KPIService`, the KPI repository port and `config/kpis.yaml` stay: the pipeline
that computes Gold uses them.

## Not decided here

- **Whether Dash draws the energy charts in Phase 5.** ADR 0004 leaves that to
  the start of Phase 5. The same question applies to it: a second place that
  draws figures needs a reason the report cannot serve.
- **The "data as of" line of the dashboard page.** None of the three endpoints
  provided it. Increment 3 designs it.

## Alternatives considered

**Keep all three.** Rejected: two sources for the same figures, with the two
costs above, and no page that needs them.

**Keep only the coverage endpoint,** as feedback after a load. Rejected: as a
footnote it is the report's job, and as feedback it is better asked by run. One
route left under `/kpis` would return live values a user could compare with the
report's copy, which is the same problem on a smaller scale.

**Keep the summary, for tiles in the app.** Rejected: the same duplication,
for a visual Power BI already has.

**Serve every figure from the API and draw it in the app.** Rejected: it
reverses ADR 0004, and means building the charts, filters and exports that
Power BI provides.

**DirectQuery now, for freshness.** Rejected for now: nothing has asked for
it. It is the first answer to weigh if something does.

## References

- [ADR 0004](0004-react-spa-entra-and-embedded-power-bi.md) — the embedded
  report, and the licence risk.
- [ADR 0006](0006-api-tokens-and-app-roles.md) — permissions, and why
  endpoints ask for one.
- [docs/api.md](../api.md) — every endpoint, and section 9 on what does not go
  through the API.
- [docs/powerbi.md](../powerbi.md) — the reporting views, the gateway, and
  what a refresh needs.
- [docs/frontend-plan.md](../frontend-plan.md) — the pages of the web app.
- Data refresh in Power BI, for the daily limits:
  <https://learn.microsoft.com/en-us/power-bi/connect-data/refresh-data>
- Refresh Dataset In Group, the REST call and its limits:
  <https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/refresh-dataset-in-group>
- Assigning users and groups to an application, for the licence a group needs:
  <https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/assign-user-or-group-access-portal>
