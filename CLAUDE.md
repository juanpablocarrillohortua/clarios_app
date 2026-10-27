# CLAUDE.md — project context and build state

Working notes for Claude Code sessions on this repository.

---

## What this project is

**Clarios Energy** — an industrial energy and gas consumption monitoring platform
for multi-plant operations. A **modular monolith** organised as a **hexagonal**
(ports and adapters) architecture around a **medallion** data flow
(Bronze → Silver → Gold), on PostgreSQL 16 + TimescaleDB.

**Phase 1 (Foundations) is built.** Every functional module is a deliberate
placeholder whose *docstring is the deliverable* — it specifies what the module
owns, what it must never do, where it sits in the Bronze/Silver/Gold flow, and
which phase implements it. Function bodies raise
`NotImplementedError("Phase N: ...")`.

Read [README.md](README.md) for setup, [docs/architecture.md](docs/architecture.md)
for the design, and [CONTRIBUTING.md](CONTRIBUTING.md) for conventions.

**Equivalent batteries (EQUS).** The plant's Excel calculation of equivalent
batteries is being moved into this database. [docs/EQUS_CONTEXT.md](docs/EQUS_CONTEXT.md)
is the source of truth for its rules and target schema; read it before touching
anything EQUS. All three layers are built. Bronze (revision `0002`) has a loader
(`make equs-load`, or `energy equs load` / `load-all`). Silver (inside revision
`0003`) has a build (`make equs-silver`, or `energy equs build-silver`). Gold
(inside revision `0004`) is views, data-quality checks and a frozen monthly close,
with `make refresh` / `refresh-all` / `resumen` / `close` / `report`.
[docs/database_overview.md](docs/database_overview.md) shows every schema with
diagrams.
[docs/equs_calculation_explained.md](docs/equs_calculation_explained.md) explains
the calculation to non-technical readers, with a Spanish translation in
[docs/equs_calculation_explained.es.md](docs/equs_calculation_explained.es.md).
[docs/equs_resumen_call_chain.md](docs/equs_resumen_call_chain.md) traces
`make resumen` through the code. Keep all three in step with any change to the
Gold views or to that call chain, and change both languages together.

**Power BI.** Revision `0006` adds schema `reporting`: twelve read-only views over
Gold for BI tools, readable by the role `reporting_reader`. `make powerbi` creates
the login (`energy reporting create-reader`) and starts `reporting-bridge`, an
opt-in container publishing 127.0.0.1:5433. Power BI Desktop connects through it
directly; the report published to the Power BI Service connects through an
on-premises data gateway on the same machine. [docs/powerbi.md](docs/powerbi.md)
is the guide.

**Chat assistant.** A chatbot adapted from another project, in three phases
described in [docs/chatbot-plan.md](docs/chatbot-plan.md): phase 1 (it answers
through a Streamlit development page, `make chat-ui`) and phase 2 (read-only SQL
over the `reporting` views, on simulated data by default, `CHAT_DATA_SOURCE`)
are built; phase 3 (a round button on the dashboard, `POST /api/chat`) is built
and waits for the owner's check in a signed-in browser.

**Energy models.** A section "Modelos" of the web app (built 2026-10-20,
[ADR 0012](docs/adr/0012-the-app-shows-the-energy-baseline-models.md), Proposed):
three baseline models of energy from production, from section 7 of
`notebooks/further_search.ipynb`, trained on the host (`make models-train`)
into `gold.baseline_models` (revision `0010`) and served by `/api/models`.

**Web app.** `frontend/` is a React single-page app (TypeScript, Vite) that Caddy
serves at `/`; Dash moved to `/dash/`. It is built in the increments of
[docs/frontend-plan.md](docs/frontend-plan.md): increment 0 (decisions),
increment 1 (the shell, with two empty sections) and increment 2 (sign-in with
Microsoft Entra ID, and roles) are done. Increment 3 (the dashboard, with the
embedded Power BI report) is built, and waits for the owner's checks in a
signed-in browser. Increment 4 (the dataset specs, `GET /api/datasets` and the
template of each) is built; [docs/dataset-specs.md](docs/dataset-specs.md) is
its guide. Increment 5 (staging, the streaming upload, the checks of a file and
the issues list, `/api/drafts` and the "Carga de datos" page) is built, and
waits for the owner's checks in a signed-in browser. Increment 6 (the reasons
and Excel fix of every issue, the corrected file uploaded again and compared)
is built, and passed the owner's checks in a signed-in browser on 2026-10-18;
grid and form entry were dropped for every dataset, and increment 9 with them.
Increment 7 (the commit of a draft into Bronze, its confirmation with a
catalogue's difference, `/api/loads`, the history and the detail of a load) is
built, and waits for the owner's checks in a signed-in browser. The decisions are in
[ADR 0004](docs/adr/0004-react-spa-entra-and-embedded-power-bi.md) (stack, Entra
ID sign-in, embedded Power BI),
[ADR 0005](docs/adr/0005-api-composition-root.md) (`api/deps.py` may import
adapters), [ADR 0006](docs/adr/0006-api-tokens-and-app-roles.md) (which token
the API accepts, roles and permissions),
[ADR 0007](docs/adr/0007-figures-reach-people-through-power-bi.md) (figures
reach people through Power BI only; the API's KPI endpoints are removed when
Phase 4 starts) and
[ADR 0008](docs/adr/0008-embedding-the-report-in-the-dashboard.md) (how the
dashboard embeds the report) and
[ADR 0009](docs/adr/0009-data-is-entered-and-corrected-in-excel.md) (data is
entered and corrected in Excel: no grid and no form) and
[ADR 0010](docs/adr/0010-a-draft-is-committed-once-in-one-transaction.md) (a
draft is committed once, in one transaction, with the loader's own SQL). [docs/sign-in.md](docs/sign-in.md) is
the guide to sign-in, and [docs/api.md](docs/api.md) the guide to the API: a
part in plain words for readers who do not write code, and a technical
reference with every endpoint. Read the plan before touching `frontend/`.

---

## Build state as of 2026-09-19 - FULLY VERIFIED

`make check` exits 0 and `make up` brings all seven services to healthy. Every
Definition-of-Done item is verified against a running stack.

| Check | Result |
|---|---|
| `make up` | **Exit 0 - all 7 services healthy** (api, caddy, dash, db, minio, prefect-server, prefect-worker) |
| Port policy | Only caddy publishes (8080/8443); everything else internal. One opt-in exception: `reporting-bridge` (profile `reporting`, `make powerbi`) on 127.0.0.1:5433 |
| `make db-upgrade` | **Exit 0** - Alembic at head `0009` against live TimescaleDB (2026-10-18; the 44 tables of Bronze, Silver, Gold and reference identical before and after, staging's row counts unchanged) |
| Schemas / tables | `bronze, gold, reference, reporting, silver, staging`; 47 tables (8 + 9 + 4 + 21 + 5), 2 materialized views, 15 views and 8 functions in `gold`, 12 views and 1 function in `reporting` |
| Extensions | timescaledb **2.30.1**, pgcrypto 1.3 |
| Hypertables | All 5 registered: `raw_readings, readings, kpi_hourly, kpi_daily, plant_daily_summary` |
| Timescale policies | 3 compression + 1 retention job created (no warnings) |
| Routes through Caddy | `/api/health` `/api/ready` `/api/docs` `/api/config` `/` `/redirect.html` `/dash/` `/prefect/api/health` `/minio/` all **HTTP 200**. `/` is the web app (any path without a file extension gets its `index.html`); `/auth/*` answers 404 by design (2026-10-12) |
| Sign-in (2026-10-12) | Without a token `/api/me`, `/api/uploads`, `/api/runs` (both removed in increment 7) and `/api/kpis/summary` answer **401**; so do a made-up token, one that names no algorithm, and a well-formed one signed with another key. A signed-out headless browser is sent to Microsoft's sign-in page with the registered redirect page, the API's scope and PKCE, from `:8080` and from the dev server on `:5173`, and Microsoft shows no error. **Not done by a session:** signing in as a real user; that is the owner's check |
| `/api/ready` payload | `degraded` (connectivity+extensions healthy, Silver empty) - correct, returns 200 not 503 |
| Placeholder endpoint | `/api/kpis/summary` -> **501** with `code=not_implemented` for a signed-in caller (in the tests); 401 without a token |
| `X-Request-Id` | Present and propagated |
| `make check` | **Exit 0** on the code of increment 7 (2026-10-18): format, lint, mypy strict (134 files), bandit, pip-audit and `frontend-check` in one run; its tests were all skipped by a slow test container (265 skipped), so `make test` was run again alone on an idle machine (row below) |
| `make frontend-check` | **Exit 0** (2026-10-18, increment 7) - Biome on 102 files, `tsc`, 368 Vitest tests in 17 files, production build of two pages (`preview.html` is not built), with the Power BI library in a file of its own (229 kB) |
| Dashboard (2026-10-13) | With the two IDs empty, `GET /api/config` answers `"powerbi": null` and sign-in asks for the API's scope only. With them set, it carries the `powerbi` object, and a signed-out headless browser reaches Microsoft's sign-in page with both scopes and no error, from `:8080` and from the dev server. The built embedding module, run in headless Edge with made-up IDs and a text that is no token, loads Power BI's frame and receives its refusal. Caddy's headers are unchanged. **Not done by a session:** seeing the report as a signed-in user |
| `make test` | **1,450 passed, 39 skipped** (38 minutes with the workbooks present, 2026-10-18, increment 7) |
| Coverage | **95.57%** (threshold 70%) |
| `make test-integration` | **118 passed** against real TimescaleDB and MinIO, with the workbooks present (2026-10-07; not re-run on its own since) |
| `make powerbi` | **Exit 0** in the stack: the bridge healthy on 127.0.0.1:5433; the login reads the reporting views from Windows through it, is refused on gold/silver and with a wrong password; `make powerbi-down` closes the port without touching the db container |
| No Spanish anywhere | 0 hits (UTF-8-aware scan, 2026-09-19). Since then Spanish appears only as data: header texts and sheet names of the QAD exports, and the table and column names of the EQUS Silver layer. Three deliberate exceptions: `docs/equs_calculation_explained.es.md`, the Spanish translation of the non-technical explanation (asked for on 2026-10-08), `frontend/src/i18n/es.json`, the texts of the web app's interface (decided on 2026-10-12, ADR 0004), and the Spanish half of the bilingual quick start at the top of `README.md` (asked for on 2026-10-26) |

The schema, `make check`, test, coverage and `make powerbi` rows were re-verified
on 2026-10-08, after the EQUS Gold layer and the reporting schema were built,
with `make check DATABASE_URL=<a reachable test database>` (the `.env` value
otherwise wins and points at the compose-internal host). The integration row
dates from 2026-10-07 and the other rows from 2026-09-19. On 2026-10-12, with
the web app added, `make up`, the routes and `make frontend-check` were verified
again. Later that day, with sign-in added (increment 2), `make up`, the routes,
`make test` and every other step of the gate were run again. On 2026-10-13,
with the dashboard added (increment 3), `make check` ran whole and passed.
`make up` was run that day too (13 minutes, the README having changed), and
the routes were checked again with the report configured and not. On
2026-10-17, with the dataset specs added (increment 4), `make check` ran whole
and passed again. With staging and the upload added (increment 5), it
passed that day, then failed twice on 2026-10-18 on two tests that passed
alone (a fixture whose bytes held the time, and the race described under
"Tests" of the web app), and passed whole once both were fixed. With the reasons and the corrected
file added (increment 6), it failed once the same day, on the API guide not
yet naming the two new routes, and passed whole once it did: the `make
check`, `make frontend-check`, `make test` and coverage rows are from that
run. With the commit and the history added (increment 7), the rows of
`make db-upgrade`, `make check`, `make frontend-check`, `make test` and
coverage were renewed on 2026-10-18; `make up` and the routes were checked
again that day (`/api/uploads` and `/api/runs` now 404, the new routes 401
without a token). The by-hand commits through `:8080` against a scratch copy
of the database were prepared but not run by a session.

**Integration tests can be skipped silently.** They start a TimescaleDB container
and wait 120 s for it. On 2026-10-06, with the machine busy, the container needed
151 s, so `make check` exited 0 with every database test *skipped* (184 passed,
63 skipped). An idle re-run of `make test-integration` passed. Check the skip
count: 39 is normal, more means the database tests did not run. The MinIO
tests do the same: on 2026-10-07 one `make check` exited 0 with 8 of them
skipped (47 skipped in all) because their container was not ready in 120 s; the
next run passed all of them.

### Bugs found and fixed during verification

Each one would have broken a fresh clone; none were visible without running the
real stack.

1. **MinIO image unpullable.** `minio/minio:latest` on Docker Hub now refuses
   anonymous pulls. Switched to `quay.io/minio/minio`, MinIO's own registry,
   pinned to `RELEASE.2025-09-07T16-13-09Z` rather than `latest`.
2. **MinIO healthcheck wrong.** `mc ready local` fails because the image ships no
   `~/.mc` alias config - the server was healthy while the probe said otherwise.
   Replaced with MinIO's own `curl -f /minio/health/live`.
3. **`LICENSE` not copied into the image.** `pyproject.toml` declares
   `license = { file = "LICENSE" }`, so hatchling could not build the wheel and
   `uv sync` failed inside the container. Added to both Dockerfile COPY lines.
4. **Windows/MSYS mangled `DASH_URL_BASE_PATHNAME`.** The Makefile exports `.env`,
   and MSYS rewrites an exported `/` into the Git install root (`D:/git/Git/`)
   when calling the native docker binary, so Dash crash-looped on an opaque
   config error. Fixed at both levels: `MSYS*_CONV_EXCL` guards in the Makefile,
   plus a `DashSettings` validator that rejects a filesystem path and falls back
   to `/`. Regression tests in `tests/unit/test_settings.py`.
5. **Alembic `Config(path)` parsed pyproject.toml as INI.** The repeated
   `[[tool.mypy.overrides]]` tables are valid TOML but a duplicate section to
   configparser, so every migration fixture errored. Use `Config(toml_file=...)`.
6. **Test fixture ordering.** The readiness tests built the app before
   `patched_settings_db` redirected the database, so they connected to the
   compose-internal host `db` from the host. Fixtures now resolve in the right
   order.

Note: a corrupt `caddy:2` image (from a pull interrupted when the disk filled)
caused `exec format error`; removing and re-pulling fixed it. Not a repo bug, but
worth recognising the symptom.

### Environment issue resolved

Docker Desktop had wedged because **C: was 100% full (0 bytes free)**; its
containerd store reported `read-only file system`. The disk image was relocated
to `D:\Docker\wsl\disk`, giving Docker ~944 GB. If Docker misbehaves again,
check free space first.

### Still not exercised

- `make seed` - runs the Phase 2 placeholder scripts, which raise
  `NotImplementedError` by design.
- `tests/e2e/` - all skipped; they are Phase 2 deliverables.
- The GitHub Actions workflows run on pull requests and on `main` or `develop`,
  not on a push to a feature branch. The owner reported CI green on the
  increment 1 commit (2026-10-12); the repository is private and `gh` is not
  installed on this machine, so a session cannot read the result itself.

## Environment specifics

| Item | Value |
|---|---|
| Repo root | `D:\Externado\Catch_2026\clarios_app` |
| uv | 0.12.17 at `C:\Users\juanp\.local\bin` — **not on the default PATH** |
| Python | 3.12.14, uv-managed (system Python is 3.13/3.14 — do not use it) |
| `.venv` | Holds notebook packages that are **not** in `uv.lock` (seaborn, matplotlib, ipykernel, jupyter). A plain `uv sync` is exact and would remove them. To add a dependency: edit `pyproject.toml`, `uv lock`, then `uv pip install <package>==<locked version>` |
| Shell for `make` | Git Bash at `D:\git\Git\bin\bash.exe`, resolved via `git --exec-path`. Selecting it as bare `bash.exe` picked up WSL's `C:\Windows\System32\bash.exe`, which has docker but no uv. `make` now works from PowerShell, cmd or Git Bash. |
| Published port | Only Caddy, `CADDY_HTTP_PORT=8080` |
| Node | v25.2.1 on this machine, with npm 11.6.2. It is an odd-numbered release that current tools no longer list as supported: Vitest 5 and jsdom 30 exclude it in their `engines`, so npm resolved Vitest 4 and jsdom 29 into the lockfile. CI and the image use Node 24 LTS (`frontend/.nvmrc`). Vitest prints a harmless `--localstorage-file` warning on Node 25 |

---

## Conventions that are easy to get wrong

**Architecture.** `domain` and `core` import nothing from `energy`. `api`,
`dashboards` and `pipelines` are siblings and must never import one another.
Enforced by [`tests/unit/test_architecture.py`](tests/unit/test_architecture.py),
which parses imports with `ast`. **Do not weaken that test to make a change
pass** — it is load-bearing. It has one exception, named in the test as
`COMPOSITION_ROOTS`: `api/deps.py`, and no other module of `api`, may import
`adapters` (ADR 0005, used since 2026-10-12 for the token verifier). A second
test fails if that list grows or another `api` module imports an adapter.

**Placeholders.** Non-test placeholders raise `NotImplementedError("Phase N: …")`.
Placeholder *tests* instead carry `@pytest.mark.skip(reason="Phase N: …")` — a
test raising would fail the suite. Keep that distinction.

**Data correctness** (these cause quiet, expensive bugs):

- Timestamps are timezone-aware UTC, always; grid-aligned to the hour.
- Convert timezone *before* resampling, never after (DST).
- Missing is never zero — enforced by a check constraint in `silver.readings`.
- Every Gold row carries `coverage`.
- Rejected rows are quarantined, never dropped.
- Corrections supersede (`superseded_at`), never overwrite.
- Positive `deviation_pct` means **worse**.

**EQUS Bronze** (revision `0002`):

- The five landing tables are append-only: `bronze.forbid_update()` rejects every
  `UPDATE`. `DELETE` is allowed (retention). `ingestion_runs` and `raw_files` are
  ledgers and stay updatable.
- Bronze never parses, types or deduplicates. `content_hash` is indexed but **not
  unique** on the landing tables; deduplication is a Silver decision.
- Payload keys are the header texts of the exports, in Spanish with accents
  (`Número de transacción`, `Fecha Efectiva`, `Núm Fact`, `Artículo Matriz`).
  They are data: never translate or normalise them.
- `source_key` on both ledgers accepts only the five EQUS keys. Registering an
  energy-readings run needs a later revision that extends the list.
- Migrations do not import application code.

**EQUS Silver** (built 2026-10-07, inside revision `0003`):

- Everything EQUS is in `silver`, named as EQUS_CONTEXT section 6 names it (decided
  2026-10-07). `reference` stays the hand-maintained energy dimensions; do not
  move the EQUS catalogues there. `silver.planta` (plant areas) is unrelated to
  `reference.plants` (sites).
- Eighteen tables and the materialized views `td_form` and `td_deco`. Natural
  keys, Spanish names, item codes as text, every number `NUMERIC`.
- **The build is SQL.** `adapters/equs_silver_sql.py` holds the statements in
  order; `adapters/postgres_equs_silver.py` runs them in one transaction through
  the raw driver cursor (several hold more than one command, which only the
  simple query protocol accepts, so nothing is bound into them). Staging is in
  `eq_*` temporary tables dropped on commit.
- The statements are fixed text: no f-strings, no bound values. The placeholder
  `(sin descripción)` is written out in them and a unit test keeps it equal to
  `SIN_DESCRIPCION`.
- Snapshots (structures, catalogues) use the latest completed run of each sheet
  and are replaced; movements use every completed run, keep the newest copy of
  each natural key and are upserted, never deleted. Catalogues that movements
  point at are upserted and never deleted either.
- A structure link is unique by business key (parent, component, validity
  dates, quantity), across sheets, and the table has that unique constraint.
- **`calculo`: a blank in sheet BAT FOR counts as `SI`**, as EQUS_CONTEXT
  specifies. One formed battery (`231001483`) therefore adds the paste of two
  assembled batteries where the workbook counts one. The documented targets of
  section 9 include it, so "fixing" it breaks the acceptance tests. It is in the
  build's report; the business has not decided.
- **The documented "790 duplicates in Datos" is not a business-key count.** By
  business key it is 70; 790 is exact repeated rows, 740 of them junk rows that
  hold only a `1`. The tests assert both numbers and say why.
- The three tables of the equation (`equs_version`, `factores_equ`, `instrucc`)
  are seeded by the migration and never written by the build. A new rule is a
  new version, not an edit: an edit changes every past month.
- Tests: `tests/integration/test_equs_silver_rules.py` states each rule on rows
  it writes itself and runs everywhere; `test_equs_silver_build.py` needs the
  real workbooks and checks section 9 of EQUS_CONTEXT to the cent, plus the
  pivots of the workbooks themselves. The equivalent batteries of a month are
  computed there by a test query that predates Gold; Gold's views now own it.
- Developing against a throwaway database from the host: use `127.0.0.1`, not
  `localhost`, in `DATABASE_URL`. With `localhost` every connection first tries
  IPv6 and each Alembic command takes minutes.

**EQUS Gold** (built 2026-10-08, inside revision `0004`):

- **No number of the equation in a view.** The views apply `silver.factores_equ`,
  `instrucc` and `equs_version`. Allowed literals: `RCT-WO`, channel `Venta`,
  warehouse `1010`, the `2101`–`3104` range of check 1, and the four `fuente`
  values. `TestGoldRevision.test_no_view_writes_a_constant_of_the_equation` reads
  the configuration and fails if a value of it appears in a view or function.
- **One definition of everything that could diverge.** `gold.en_vigor` is the only
  "version in force in a month" rule (the version whose validity contains the
  month's first day). `v_reporte_qad_regla` and `v_unit_ship_regla` pair rows with
  rules; `v_cantidad_cuenta` and every range function sum those two, so a month and
  a range covering it cannot disagree.
- **Nothing in Gold is materialized.** A fiscal year computes in about a second.
  The only stale objects are `silver.td_form` and `td_deco`; `refresh-gold`
  refreshes them `CONCURRENTLY` in autocommit and runs `ANALYZE`. If a view is ever
  materialized, the refresh must cover it.
- **The close is frozen.** `gold.resumen_equ` changes only through
  `close-month --reclose`, which copies the old rows to `resumen_equ_historial`;
  every close and reclose is a row of `cierre_mes` with the data-quality counts and
  whether blocking warnings were accepted. Refreshes never touch these tables.
- **Layout is data.** `gold.secciones_reporte` (column F of RESUMEN EQU, plant
  areas) and `gold.filas_resumen_equ` (row numbers) drive the report's order and
  labels; `domain/equs_gold.py` only nests and rounds.
- **Refresh and close share an advisory lock** (key 7215590330), taken under
  `lock_timeout`; a second one waits up to `EQUS_LOCK_TIMEOUT_SECONDS` and exits 3.
- **Exit codes of the Gold commands and `equs-report`:** 0, 1 invalid or refused,
  2 database, 3 lock. `main()` runs Typer with `standalone_mode=False` and maps
  usage errors to 1 (Typer 0.27 bundles its own Click: catch `typer.TyperException`,
  not `click.UsageError`). CLI logs go to stderr; stdout carries only the result.
- **Check 4 finds 1,588 FY26 rows, not "about 1,495".** The 1,495 are exactly the
  covers and handles of product lines 1200 and 1202; the other 93 are `150000015`,
  truck handles (ACCMED, line 1305). Matching 1,495 would need product lines in the
  view. **Check 3 finds 34 FY26 rows** (16 imported batteries with 0 decoration
  pieces), none in September: closing those months needs `--accept-warnings`.
- `make` targets only call the CLI. `RUNNER=host` runs them on the host and
  `ENERGY=` names the interpreter (the tests pass `ENERGY="<python> -m energy"`).
- Tests: `test_equs_gold_rules.py` builds a small world in Silver and runs
  everywhere (including the lock, two refreshes at once, a reader during a
  refresh, and the make targets' guards); `test_equs_gold_build.py` needs the
  workbooks and reconciles row by row with column H of RESUMEN EQU read from the
  workbook, asserting each known difference at its exact value.
- The engine's connect hook (`db/session.py`) commits its `SET TIME ZONE`. It
  used to leave the connection in a transaction, which made autocommit connections
  impossible and let the pool's rollback undo the setting after the first use.

**Reporting schema and Power BI** (built 2026-10-08, revision `0006`):

- **`reporting` holds views only, and they restate Gold.** Every number comes
  from a Gold view, so Power BI and `make resumen` cannot disagree.
  - `equs_mensual` is `v_resumen_equ` plus a zero row for each driver nothing
    fed.
  - `aporte_cuenta` adds up to it exactly.
  - `equs_poly_mensual` and `calidad_datos_mensual` repeat the grouping of the
    range functions over every month.
- **Figures reach people through these views and Power BI, and nowhere else**
  (ADR 0007, decided 2026-10-13). The API serves no analytical figure.
  - The three KPI endpoints (`api/routers/kpis.py`) are **not to be
    implemented**. Their docstrings are no longer a specification. Phase 4
    removes them as its first step and delivers the energy KPIs as new
    `reporting` views, each row with its `coverage`.
  - Until Phase 4 they stay untouched, answering 501, and their rows stay in
    the table of `docs/api.md`: the owner asked that nothing be built before
    the phase itself.
  - What a load did, before the report is refreshed, is read in its detail
    (`/api/loads/{run_id}`, built in increment 7), not by a KPI route.
  - `dashboard:view` only makes the app show the dashboard section. Who sees
    the report is decided in Power BI, by access to the workspace.
  - A model on shared capacity may be refreshed eight times a day, scheduled
    refreshes and those started through the REST API together. The workspace
    is on shared capacity.
  - Before building anything in Phase 4, ask the plant how fresh energy
    figures must be. "Within minutes" reopens the ADR.
- **Never call a Gold function from a reporting view.** Views read their tables
  with the owner's rights, but a function runs with the *caller's*: the reader
  would get *permission denied*. Build on Gold views (`gold.en_vigor` is fine:
  it touches no table and is inlined).
- **A new reporting view grants itself.** Its revision must also
  `GRANT SELECT … TO reporting_reader`. There are no default privileges.
- **Roles are cluster-wide.** The revision creates `reporting_reader` only if
  missing, and the downgrade drops it only when no other database still grants
  it something. Tests that create a login drop it in their teardown.
- **`ALL_SCHEMAS` includes `reporting`, last,** so the test teardown drops it
  before `gold`.
- **The login is not created by the migration.** `energy reporting
  create-reader` reads `REPORTING_DB_USER` / `REPORTING_DB_PASSWORD`, hashes the
  password client-side (SCRAM), creates or alters the login in one transaction,
  and sets on the role:
  - `default_transaction_read_only`;
  - `search_path = reporting`;
  - a 10-minute `statement_timeout`;
  - `equs.umbral_variacion`, so the data-quality counts match the CLI's.

  It refuses an existing role that may do more than read: a superuser, the
  application's own role, or an owner of a schema or relation.
- **The bridge is `alpine/socat`, under compose profile `reporting`.** `make up`
  never starts it. `make powerbi` = `create-reader`, then `up --wait` the bridge;
  `make powerbi-down` removes it. It defaults to 127.0.0.1 because the database
  speaks no TLS, and to port 5433 because 5432 is often taken on the host
  (here by another project's database).
- **Tests:**
  - `TestReportingSchema` in `test_equs_gold_rules.py`: the views against Gold
    on the synthetic world, and the login's reach (permission denied on
    bronze/silver/gold, read-only, password renewal, refusals);
  - `TestReportingRevision` in `test_db_migrations.py`: the views, the role,
    the grants;
  - one test in `test_equs_gold_build.py` on the FY26 figures.
- **Speed with FY26, warm:** every view answers in under a second, except
  `calidad_datos_mensual` (about 3 s). Import mode is the documented one.
- **The published report reads through a gateway**, set up by the owner and
  checked on 2026-10-12 with read-only calls to the Power BI REST API.
  [docs/powerbi.md](docs/powerbi.md) marks each fact as read from the API or
  taken from the owner's description. The on-premises data gateway
  `clarios-local-gw` runs on the host as a Windows service, outside compose. Its
  connection `clarios-reporting` dials `127.0.0.1:5433` with the login
  `powerbi`, unencrypted (the login's name and the encryption setting are the
  owner's word: the API returns neither).
  - **Keep people and machines out of the repository:** no account addresses,
    tenant names, machine names, identifiers of the tenant, workspace, model or
    gateway, and no install paths of the Azure CLI. Write "the gateway admin
    account in the tenant" and "the machine that runs the stack".
  - The API checks are read-only: `GET` only, no refresh triggered, nothing
    changed in the Service, the gateway or the Entra app.
  - A refresh in the Service needs the stack, the bridge and the gateway
    running; `make powerbi-down` stops it. No refresh is scheduled.
  - The Service matches the connection by server and database name, exactly
    `127.0.0.1:5433` and `energy`: never `localhost`, and
    `REPORTING_BIND_ADDRESS` / `REPORTING_PORT` keep their defaults.
  - Publishing the report again from Desktop drops the mapping.
  - A new `REPORTING_DB_PASSWORD` must also be entered in the connection.

**Web app** (`frontend/`, increment 1 built 2026-10-12):

- **No business rule in TypeScript.** The app renders what the API tells it.
  Validation, messages about data and calculations stay in Python.
- **Texts are keys of `frontend/src/i18n/es.json`**, in Spanish; keys, code and
  comments are English. `i18next.d.ts` types the keys, so a missing one fails
  `tsc`. Tests read the texts from that file and never repeat them.
- **The Caddyfile owns the routes.**
  - `/api`, `/prefect` and `/minio` go to their services; `/dash/*` goes to
    Dash with the prefix kept.
  - `/auth/*` answers 404 from Caddy. It is the "Web" redirect URI of the Entra
    app registration, reserved for a server-side sign-in nothing implements.
  - Everything else is the app. A path with a file extension is a file or a
    404; any other path gets `index.html`. So app paths carry no extension.
  - `admin off` means Caddy cannot reload: after editing the Caddyfile,
    `docker compose restart caddy`.
  - Never add a `Cross-Origin-Opener-Policy` header: MSAL's redirect page
    (increment 2) stops working with it. Nor a
    `Cross-Origin-Embedder-Policy`: it would block the Power BI frame.
  - No Content-Security-Policy is sent. One belongs to increment 13, proved in
    a signed-in browser; section 10 of the plan lists what it must allow.
  - The dev server forwards the stack's paths as the Caddyfile matches them,
    by whole segment: `frontend/src/stackRoutes.ts`, read by `vite.config.ts`
    and kept by a test. Until 2026-10-13 it matched by prefix, so `/dash` took
    `/dashboard` and that page stayed blank on the dev server. A new route of
    the Caddyfile needs its entry there.
- **The look is design tokens** (restyled on 2026-10-16 in the Power BI
  report's palette; dark only, `forceColorScheme`).
  - `frontend/src/styles/tokens.css` is the only file with colour values,
    and also holds the gradients, glows, radii, type and motion values.
    `checks/designTokens.test.ts` fails for a hex value or colour function
    anywhere else in `src/` or the two HTML pages. The favicon is the one
    exception: an SVG cannot read CSS variables.
  - `theme.ts` points Mantine at the tokens (`cssVariablesResolver`,
    `variantColorResolver`, and component classes in
    `styles/mantine.module.css`). `styles/app.module.css` holds the app's own
    pieces: the canvas, `components/Panel.tsx` (`tone` error, warning or
    info; six `accent` variants nothing uses yet), the status dot, the
    report's frame.
  - White on the pink is below AA (3.44:1): filled controls carry
    `--ce-text-on-accent`. Pink is the primary action, the active nav item
    and focus, never an error (`--ce-error` is a separate red).
  - The report's frame keeps `color-scheme: normal`. Under the dark page, a
    frame whose document is light gets an opaque white backdrop from the
    browser, which would show wherever Power BI leaves the frame unpainted.
    The report itself is not transparent: it draws its own pages (below,
    under the dashboard).
  - Glows and movement turn off under `prefers-reduced-motion`; materials go
    solid under `prefers-reduced-transparency`; borders strengthen under
    `prefers-contrast: more`. Motion uses the spring of `--ce-ease-spring`
    (critically damped, no bounce).
- **Dash's base path is fixed in compose** (`DASH_URL_BASE_PATHNAME: /dash/`),
  not read from `.env`, because it must equal the Caddyfile's route. The image's
  healthcheck probes the same variable.
- **Entra ID redirect URIs.** The app has two, under "Single-page
  application": `http://localhost:8080/redirect.html` (after sign-in) and
  `http://localhost:8080/signed-out` (after sign-out). Both are also constants
  in `frontend/src/paths.ts`, so neither can be renamed in one place only. Entra
  ignores the port of `localhost` URIs, so they also cover the Vite dev server
  on 5173; two `localhost` URIs that differ only by port must never be
  registered. `ENTRA_CLIENT_SECRET` is in `.env` and passed to no container.
- **Packages.** Installed with a one-week cooldown
  (`npm install --before=<date>`), exact versions, `ignore-scripts=true` in
  `frontend/.npmrc`. `npm ci` in Docker and CI installs the lockfile as is.
- **Folder names.** The root `.gitignore` ignores `lib/`, `build/`, `dist/`,
  `env/` and `var/` anywhere: a source folder named so never reaches git.
- **`make frontend-check`** is `biome ci`, two `tsc` runs, `vitest run` and
  `vite build`. `make check` runs it before the Python tests.
- **Tests** render the whole app in jsdom through `renderAppAt()`, in React's
  strict mode as `main.tsx` does, with a stand-in for sign-in (`fakeAuth()`),
  for the API and Power BI's REST API (`stubApi()`) and for the embedding
  library (`fakeEmbed()`); `frontend/src/test/setup.ts` supplies `matchMedia`
  and `ResizeObserver`, which Mantine's shell needs and jsdom lacks. The app
  starts with three requests, so a test waits for what it looks at (`findBy…`).
  A test that moves the clock uses `vi.useFakeTimers({ shouldAdvanceTime: true
  })`, or `findBy…` never returns. What an effect's clean-up does (the
  dashboard removing the report) is awaited with `waitFor`: React may run it
  after the next screen is already drawn, and under a full run it once had
  not.
- **The shell has two layouts, switched at Mantine's `sm` (768 px) by
  `layout/useWideLayout.ts`** (2026-10-16).
  - **Wide:** the sections are tabs in the header, and there is no sidebar
    and no footer. The API's state is a dot in the header: words only when
    it is offline, otherwise a tooltip and text for screen readers
    (`data-quiet`). A page puts an action in the header through
    `layout/HeaderAction.tsx`, as the dashboard does with its link to Power
    BI, and keeps its `h2` for screen readers only.
  - **Narrow:** burger, drawer and footer, as before.
  - **Only one layout is ever rendered,** never both hidden by CSS: the tests
    expect one navigation and one of each link.
  - **Tests see the narrow layout,** because `setup.ts` answers every media
    query with no. `onWideScreen()` in `test/render.tsx` makes `min-width`
    queries match.

**Sign-in and roles** (increment 2, built 2026-10-12;
[docs/sign-in.md](docs/sign-in.md), ADR 0006):

- **The API accepts one kind of token:** a version 2.0 Entra ID access token of
  the project's tenant, issued for the app registration, with the scope
  `access_as_user`. Never the ID token. The checks and their order are the
  module docstring of `adapters/entra_token_verifier.py`.
- **A route is protected unless its path is in `PUBLIC_PATHS`** (`api/main.py`).
  Routers are mounted there with `dependencies=[Depends(requires(Permission.…))]`.
  `tests/unit/test_api_auth.py` calls every published operation without a token
  and fails for one that answers. `/ingest` is the device route (API keys,
  Phase 3) and is skipped by name.
- **Endpoints ask for a permission, never for a role.** `GRANTS` in
  `api/security.py` is the only place where a role is given a meaning, and a
  test restates it. Nothing is inherited: a role is listed on every row it
  applies to. `device` is never taken from a person's token.
- **No default role.** A signed-in user without one gets an empty permission
  list from `GET /me`, and the app shows its no-access page.
- **A refusal does not say why.** The 401 body is fixed; the reason
  (`wrong_version`, `expired`, …) is in the log line `caller not authenticated`.
  A token is never logged. The access log names the caller by `subject`, the
  person's object ID.
- **The signing keys** are fetched from Entra ID, kept a day, and refetched for
  an unknown key at most every five minutes. With no keys and no answer the API
  says 503, not 401. The api container needs outbound access to
  `login.microsoftonline.com`.
- **`ENTRA_TENANT_ID` and `ENTRA_CLIENT_ID`** reach the api container through
  compose and the app through `GET /api/config`. They are in no image and no
  file of the repository; tests use made-up GUIDs. `EntraSettings` has no field
  for the secret. Empty values mean "not configured": the stack still starts.
- **Tests never reach Entra ID.** The `token_verifier` fixture replaces the
  bound adapter and `auth_headers("viewer")` gives a signed-in caller's
  headers. A test that calls a guarded route with a token and without that
  fixture would use the developer's real `.env`: set the variables empty first
  (`entra_env` in `test_api_auth.py`).
- **A test module never imports `energy.api.main` at its top.** Importing it
  builds the app, which caches the settings. pytest imports every test module
  before running any, so the cache would hold the database address of `.env`
  (`db`, unreachable from the host) and every database test would fail with
  "failed to resolve host 'db'". It happened on 2026-10-12: 175 errors. Import
  inside the test, as `conftest.py` does. The database fixtures now also clear
  the cache after pointing `DATABASE_URL` at the test database.
- **`docs/api.md` lists every endpoint, and a test keeps the list complete.**
  `tests/unit/test_api_reference.py` fails for an operation of the API that the
  guide does not name, and for one it names that no longer exists. The guide
  writes an operation in a code span, as `GET /api/me`: a new endpoint needs
  its row, and a planned one must not be written that way. Its sample answers
  were copied from real calls made with made-up identifiers; keep them so.
- **The API is not the only way into the database,** and no document may say
  so. Section 9 of `docs/api.md` lists every way in: the API, the command line
  (which loads exports and runs every calculation), and Power BI through
  `reporting-bridge`. The
  sign-in and the roles protect the first only. Add a row there when a new way
  in appears.
- **Four things the API does that its docstrings do not say** (found on
  2026-10-12, described in `docs/api.md`, not changed):
  - the device routes ask for no real credential yet: the watermark route
    takes none, and the readings route takes the Phase 1 stand-in, which
    accepts a missing key outside staging and production. Both answer 501;
  - a 422 and the 404 of an unknown path use FastAPI's `{"detail": …}`, not
    the error envelope;
  - no handler fills `ErrorResponse.request_id`: the ID is in the
    `X-Request-ID` header;
  - an unforeseen 500 is plain text without that header.
- **In the app, only `auth/msalAuthClient.ts` imports MSAL.** Everything else
  uses `AuthClient`. `Session.tsx` draws nothing of the app until the settings,
  the account and `GET /api/me` are known; pages read them with `useSession()`.
  The app goes to Microsoft by itself once per tab; coming back without an
  account it asks, so a failure cannot loop.
- **Open the app as `localhost`, not `127.0.0.1`:** the redirect URI is built
  from the address bar, and only `localhost` is registered.
- **The app registration is the owner's to change.** Read it back with the
  Azure CLI session, `GET` only (`az ad app show`), and print no identifier.
- **Not covered:** `/dash/`, `/prefect/` and `/minio/` are served without this
  sign-in. That must be closed before the stack runs on a shared host.

**The dashboard and the embedded report** (increment 3, built 2026-10-13;
section 10 of [docs/frontend-plan.md](docs/frontend-plan.md), ADR 0008,
[docs/powerbi.md](docs/powerbi.md)):

- **The browser loads the report from the Power BI Service** with the signed-in
  user's own token ("embed for your organization"). The API never calls Power
  BI and holds no Power BI secret. Its whole part is `GET /api/config`, which
  names the report.
- **`POWERBI_WORKSPACE_ID` and `POWERBI_REPORT_ID`** are in `.env`, reach the
  api container through compose and the app through `"powerbi"` of
  `GET /api/config`, with the scope and two addresses built from them
  (`core/constants.py` holds Power BI's hosts). Empty means "not configured":
  the dashboard says so and the rest of the app works. They are in no image
  and no file of the repository; tests use made-up GUIDs. Nothing depends on
  the report's name.
- **A test that reads `GET /config` empties those two variables first**
  (`entra_env` in `test_api_auth.py` does), or it would read the developer's
  real IDs from `.env`.
- **One permission:** `Report.Read.All` of the Power BI Service, delegated,
  with admin consent. The app names that scope at sign-in
  (`extraScopesToConsent`), so removing the permission can stop sign-in.
- **In the app, only `powerbi/powerBiReportEmbed.ts` imports `powerbi-client`.**
  Everything else uses the `EmbedReport` interface of `powerbi/reportEmbed.ts`.
  `main.tsx` imports that file on demand, so the library is a file of its own
  in the build. Its test runs the real library in jsdom.
- **`getServiceToken()` never sends the page away,** unlike `getApiToken()`.
  When Microsoft needs the user it rejects with `InteractionNeeded`, and the
  dashboard shows a message with a button. Keep it so: a consent that cannot
  be given would otherwise loop.
- **The app sends Power BI nothing but `GET`.** `powerbi/reportLookup.ts` asks
  for the report before embedding it: the embed address carries a `config`
  parameter only Power BI knows, and a refusal arrives as a status and the
  header `X-PowerBI-Error-Info`.
- **`powerbi/reportProblem.ts` is the one place where Power BI's answers get a
  meaning.** Seen on 2026-10-13: 401 `GroupNotAccessible` for a workspace the
  caller is not in (a wrong workspace ID looks the same), 404
  `PowerBIEntityNotFound`, 403 `InvalidToken`. **How Power BI refuses a user
  without a licence is not known:** that user gets the `refused` message,
  which names the licence. Add the code to `KIND_OF_CODE` once it is seen.
- **The page never covers or removes the frame on silence alone.** The line
  about loading sits above the frame. After 60 seconds without `loaded` or an
  error it becomes a notice with a retry (`dashboard.slow`), and the frame
  stays. Whether Power BI sends an event when it draws a notice of its own in
  the frame (a missing licence, for instance) is not known, so nothing may
  hide the frame for long. It waits out of sight only until `loaded`, 10
  seconds at most (`REVEAL_AFTER_MS`), because Power BI's own loading page is
  white.
- **The report draws its own background** (`BackgroundType.Default`). Its
  pages are dark by design. Embedded as transparent, they lost that
  background and their light text stood on white (the owner's signed-in
  check, 2026-10-16). Below a page shorter than the frame, Power BI draws
  the report's wallpaper, which every page sets (black). It is changed in the
  report, not in the app.
- **The whole page is in view at once, with none of Power BI's panes**
  (ADR 0008, decision 10, 2026-10-16).
  - **Settings:** `LayoutType.Custom` with `DisplayOption.FitToPage`; the page
    bar and filter pane are hidden.
  - **The frame takes the open page's shape.** `onPageSize` of the embed
    interface fires on `loaded` (`getActivePage().defaultSize`) and on
    `pageChanged`. `ReportFrame` sets `--report-ratio` and `data-fitted`,
    and CSS makes the frame `min(100cqw, 100cqh * ratio)` in a size
    container (`.reportArea`). Until Power BI says, the frame fills the
    area. The shape is never written in the app.
  - **"Pantalla completa"** is shown on wide screens where
    `document.fullscreenEnabled`, and calls `requestFullscreen()` on the
    area, not on Power BI's frame.
  - **Fit to width was tried and withdrawn the same day:** no bands, but the
    owner had to scroll about 200 pixels, and they want the whole page at a
    glance. A desktop window is wider than the 16:9 page (1280 × 720, all
    eight), so the app's background shows on both sides.
  - **The report must keep a button to every page on every page and its
    filters as slicers on the page.** Read from its definition on
    2026-10-16, it does, and each page's one pane filter is hidden from
    viewers. A filter made visible in the pane would not be seen in the app.
  - How that was read: `GET …/reports/{id}/Export?downloadType=LiveConnect`
    returns the report without its model or data. It was parsed in memory
    and nothing was written. The pages API carries no size.
- **Seen in a real browser on 2026-10-13,** with the built module, made-up
  IDs and a text that is no token: the frame loads from `app.powerbi.com`
  under Caddy's headers, and Power BI answers `LoadReportFailed`, status 403,
  "Could not resolve cluster", twice in the same instant. A frame's error
  therefore reaches the page with its status and sentence (`EmbedError.detail`),
  quoted on the code line, because that name covers more than one cause.
- **The library raises its errors as page events named `error` that bubble.**
  `powerBiReportEmbed.ts` stops them at the report's element: at the window
  they pass for uncaught errors with whatever listens there (the dev server's
  client did, and an error reporter would).
- **That browser check needs no sign-in and can be repeated:** headless Edge
  driven through its debugging port, on a page of the app that does not go to
  Microsoft (`/signed-out`), importing the built file of the embedding module.
  The same browser sent the page's own `GET` to Power BI's REST API with a
  text that is no token: the request passes the browser's cross-origin rules,
  and the page reads the status, `X-PowerBI-Error-Info` and `RequestId`.
- **`powerbi/tokenRenewal.ts` keeps the token fresh:** a check every 30
  seconds that renews when under 10 minutes remain, the tab becoming visible,
  and Power BI asking (`accessTokenProvider`, or an error named
  `TokenExpired`). The library has no `tokenExpired` event.
- **`/dashboard?tokenLife=60` exists on the dev server only.** It makes every
  check renew, and is compiled out of the production build.
- **The dashboard's query does not use TanStack's `signal`.** With it, strict
  mode's first unmount cancels the request and a second token is asked for.
- **A viewer needs three things:** an app role, access to the workspace, and a
  Power BI licence. On 2026-10-13 the workspace had one member, the admin
  account, and no account had both a role and access.
- **The "data as of" line is the report's,** not the app's: reading a model's
  refresh history needs Write permission on it.
- **Not done by a session:** seeing the report as a signed-in user, and seeing
  it outlive its first token. Both are the owner's checks, listed in the plan.

**Dataset specs** (increment 4, built 2026-10-17;
[docs/dataset-specs.md](docs/dataset-specs.md)):

- **One YAML file per dataset in `config/datasets/<key>.yaml`, format version
  1.** Two pilots: `lineas_produccion` (a hand-kept catalogue, 10 columns) and
  `qad_facturas` (a QAD export, 49 columns). Nothing applies their rules to a
  file yet: that is increment 5.
- **The API reads them in `create_app()`,** through `deps.load_dataset_catalogue`
  (the adapter `adapters/yaml_dataset_specs.py`). `energy.api.main` builds the
  app at import, so a malformed spec stops uvicorn before it serves, with a
  `DatasetSpecError` listing every problem as `<file>: <path>: <problem>`. Any
  test that builds the app reads `config/datasets` relative to the working
  directory (the repository root); set `CONFIG_DIR` to point elsewhere.
- **`domain/dataset_spec.py` holds the meaning,** standard library only: the
  format, every check, the catalogue. The adapter only reads files, refusing a
  key repeated in one YAML mapping, which `yaml.safe_load` would keep silently.
- **A spec writes headers, never payload keys.** Keys are derived with
  `landing.header_keys`, as Bronze derives them (the second `Almac` of the
  invoices is `Almac #2`). Natural keys and the period column name keys.
- **Copy headers from the workbook by script** (print them with `ascii()`):
  `Tiempo de configuración  (D H:M:S)` has two spaces, and accents are
  invisible on screen. Quote allowed values: YAML reads an unquoted `No` as
  false.
- **`tests/unit/test_dataset_specs_committed.py` ties each spec** to the
  loader's `ROUTES`, to the Silver SQL (every `payload ->> '…'` key Silver reads
  from the dataset must be a spec key; the SQL stays fixed text, so this is a
  test, not a generation), to `es.json` (the label keys), and to the real
  workbook's header row when `data/raw_excels` holds it.
- **`GET /api/datasets` and `GET /api/datasets/{dataset_key}/template` need
  `loads:write`.** The answer carries no Bronze or Silver names. The template
  (`adapters/xlsx_dataset_template.py`, openpyxl) is one sheet with the headers
  and no data; formats and drop-down lists sit on the columns, never in cells,
  and a test reads every template back with the Bronze reader. No instructions
  sheet: its Spanish would have to live in Python.
- **openpyxl ships no types.** Its imports carry `# type: ignore[import-untyped]`
  in the adapter, rather than an override in `pyproject.toml`, which would
  force the slow image rebuild.
- **`energy datasets check [--config-dir]`** reads the specs as the API does
  (`pipelines/flows/dataset_specs.py`) and exits 1 with every problem.
- **The rules were measured on FY26 (2026-10-17):** every row satisfies each
  pattern and range. Codes that may one day hold a letter (items, customers)
  have no pattern, so a new code cannot block a monthly load.

**Staging and drafts** (increment 5, built 2026-10-17; revision `0007`,
`/api/drafts`, the "Carga de datos" page):

- **A draft is one upload, owner-only.** Schema `staging`: `drafts`,
  `draft_rows`, `draft_issues`, `draft_rule_counts`. A working area, not a
  layer: no foreign key into Bronze, `reporting_reader` and `PUBLIC` get
  nothing. Every read filters by `created_by` and `expires_at`; someone else's
  draft is a 404. `energy drafts purge [--dry-run] [--draft-id]` removes
  expired drafts (30 days after the last work) and their original unless
  another draft or `bronze.raw_files` needs it. Nothing schedules it.
- **Bronze is unchanged by an upload.** The original is archived at once, under
  `archive_object_key` (the Bronze loader's key) in `bronze-raw`, but
  `raw_files` is registered by the commit of increment 7 (decided 2026-10-17,
  amending plan §6 step 1). `postgres_draft_repo.py` names no `bronze.` object
  and a test reads it to keep it so.
- **The upload is the raw body, not a form,** streamed: the async router pulls
  each chunk from the event loop (`anyio.from_thread.run`) for the service,
  which runs in a worker thread; `UploadGuard` counts, checks `PK`
  and stops at 300 MiB; MinIO receives 8 MiB parts under `incoming/<draft_id>`,
  then a server-side copy goes to the hashed key. **`put_object` must keep
  `num_parallel_uploads=1`:** minio-py's default (3) queues more parts, which
  broke the memory bound and gave `IncompleteBody` in a test. **Keep the parts
  at 8 MiB** (`UPLOAD_PART_BYTES`): minio-py builds a part by appending
  chunks and copies it again, so the peak is about four parts. Through Caddy,
  16 MiB parts took the api container 81 MiB over idle for a 300 MiB file,
  8 MiB parts 47 MiB; measured alone, 80 MiB and 34 MiB (2026-10-17).
  Every reader handed to `put_object` must return at most `size` bytes: one
  that returns more makes minio-py's `read_part_data` read the whole stream
  into one part.
- **300 MB means 300 MiB** (`MAX_FILE_BYTES`, 314,572,800), checked by the
  browser (from `limits` of `GET /datasets`), by Caddy (`request_body` on
  `POST /api/drafts`; 10 MiB on the rest of `/api`; `read_body 30m`) and by
  the API. `tests/unit/test_caddyfile.py` ties the Caddyfile to the constant.
- **Validation runs in the request for files up to 5 MiB**
  (`CHECK_IN_REQUEST_MAX_BYTES`); a larger one answers 501 `not_implemented`
  until increment 12. It downloads the original to a temp file (hash checked),
  judges the zip directory (`workbook_limits.IN_REQUEST`) and unpacks every
  entry through a counter (`zip_package.py`), then reads with calamine
  `from_path`. One transaction, `FOR UPDATE NOWAIT` on the draft (a second
  validation is 409), replacing earlier results.
- **The rules judge what Bronze would store, as Silver reads it.**
  `landing.sheet_payloads` feeds both `landing_rows` and `check_sheet`; values
  are trimmed of spaces (`eq_txt`); numbers follow `SILVER_NUMBER`, which a
  test keeps equal to `pg_temp.eq_num`; a `date` column takes date cells only.
  The first 1,000 issues per rule and column are listed; every row is counted.
- **Rule IDs, not sentences, cross the API.** The sentences are
  `loads.rules.<id>` of `es.json`; `tests/unit/test_draft_rules.py` fails for
  a rule without one. In the app, `loads/texts.ts` is the one place that looks
  up keys the server names (untyped, with a fallback).
- **In the app, only `api/upload.ts` uses `XMLHttpRequest`** (fetch has no
  upload progress). A draft has its own address, `/loads/drafts/<id>`, so a
  reload or a new sign-in resumes it; arriving from an upload it validates
  once (a ref guards strict mode).
- **`frontend/preview.html` is for the dev server only:** the loading pages with
  stand-ins for sign-in, the API and the upload (`src/preview/stage.ts`,
  `?scenario=broken|clean|many|large|busy|refused`, and for a correction
  `corrected|same|allFixed|rulesChanged|previousGone`). `vite build` does not
  build it, `checks/previewPage.test.ts` keeps it so, and `:8080` answers 404.
- **Synthetic files:** `uv run python scripts/make_test_workbooks.py <folder>
  [--large]` writes broken files with their manifest, and padded packages of
  12 MiB, 300 MiB - 4 KiB and 301 MiB. No real data. `build_workbook` fixes
  every timestamp in the package (openpyxl and `zipfile` write the time to
  the second), so one file built twice has one hash; the duplicate and purge
  tests rely on it, and one failed in a full run before it was so. The
  builders are `tests/fixtures/*.py`: `.gitignore` ignores everything else in
  that folder (generated files), and until 2026-10-18 ignored them too, which
  no local run could show. ruff respects `.gitignore`, so it never linted them
  either: the committed `c7ab29e` fails `ruff check .` on them (fixed in the
  tree on 2026-10-18).

**Reasons and corrections** (increment 6, built 2026-10-18; revision `0008`,
[ADR 0009](docs/adr/0009-data-is-entered-and-corrected-in-excel.md)):

- **Data is entered and corrected in Excel.** No cell is edited in the app, and
  there is no grid and no form for any dataset: `EntryMode` has `upload` only,
  and the spec checker refuses `grid` and `form`. A file with problems is fixed
  in Excel and uploaded as a *correction* (`POST /api/drafts/{draft_id}/corrections`):
  a draft of its own, with its own archived original, whose
  `previous_draft_id` names the draft it corrects. That link is not a foreign
  key, so a correction still knows it had an earlier file after the purge.
  Uploading a correction extends the earlier draft's expiry.
- **Every issue has reasons, most likely first, ending with its rule's own**
  (`default.<rule>`). Detectors are pure functions in
  `domain/draft_reasons.py`, deterministic, from the file and the spec alone;
  `draft_rules.py` calls them where the cell, its type, the row and the
  headers are in hand, and `IssueCollector` gives an issue without reasons its
  rule's own and counts every issue by its first reason
  (`draft_reason_counts`). 34 reasons: 18 from detectors, 16 rules' own.
- **Texts:** `loads.reasons.<id>.why` and `.fix` in `es.json`. A rule's own
  reason has a `fix` only; its "why" is the rule's message. The fixes name
  Spanish-language Excel for Windows (decided 2026-10-18).
  `tests/unit/test_draft_reasons.py` fails for a reason without its texts.
  In the app, `loads/texts.ts` (`reasonWhy`, `reasonFix`) is the one place
  that reads them.
- **`cell.text_damaged` is a warning:** a code in scientific notation or with
  `.0`, invisible characters (a line break is allowed in free text), or text
  read in the wrong encoding, in a text cell no other rule judged. The FY26
  scan of 2026-10-18 found none in the 14,458 invoice rows or the 64 lines.
  Its test run on every text cell of every workbook of `data/raw_excels`
  (3.9 million) found one: a non-breaking space inside a description, cell B9
  of sheet `Clasif_Poly`, which has no spec yet. Expect that warning when it
  gets one.
- **An escape such as backslash-u-00a0, written through the editing tools,
  lands as the real character.** Build invisible characters with `chr()` in
  Python, as `tests/fixtures/workbooks.py` (`NBSP`) does, and check with ruff
  (RUF001, PLE2515).
- **Matching a correction's issues:** same rule, column and `match_key`. The
  key is the row's natural key loosened (case, spaces, accents and
  non-breaking spaces ignored, so fixing those keeps the row) with its
  occurrence among rows of that key, or `row:<n>` when the key is
  incomplete. A rule and column over the 1,000 cap on both sides is compared
  by count only (`by_count_only`), never folded into cleared, remaining and
  new, which are therefore exact. `domain/draft_comparison.py` holds the
  arithmetic; the repository's SQL and the in-memory stand-in apply the same
  `compared_by_row` test.
- **The spec's digest** (`spec_digest`) is stored at validation. Validating a
  correction validates the earlier draft again when it was judged by another
  spec; `previous_revalidated` is read from the two validation times, nothing
  stored. If that cannot be done, the comparison's state is `rules_changed`.
- **The draft page validates a draft it arrives at once, per draft** (a ref
  holding the draft's ID): a correction moves the same page to a new draft.
- **jsdom has no `scrollIntoView`;** `src/test/setup.ts` stubs it. Mantine's
  select opens from the keyboard in tests (focus, ArrowDown, Enter); a click
  does not open it in jsdom.

**Commit and history** (increment 7, built 2026-10-18; revision `0009`,
[ADR 0010](docs/adr/0010-a-draft-is-committed-once-in-one-transaction.md)):

- **One transaction, with the loader's SQL.** `adapters/postgres_load_committer.py`
  runs `SELECT_FILE`, `INSERT_FILE`, `INSERT_RUN`, `COMPLETE_RUN`,
  `LANDING_COLUMNS` and `TABLE_WITH_DATASET_KEY` of `postgres_bronze_repo.py`
  (made public in increment 7, their text unchanged) on one connection, with
  the staging update. Never call the `BronzeLandingRepository` methods from
  it: each commits by itself. A test in `test_load_service.py` scans
  `src/energy`: only those two adapters write a `bronze.` table.
- **No failed run and no `committing` state.** Any failure rolls everything
  back; the draft stays validated with its rows. The draft is locked
  `FOR UPDATE NOWAIT` (a second request: 409 `draft_busy`); a committed draft
  answers every later commit 200 with its load, whatever the body.
- **One commit per dataset:** `pg_advisory_xact_lock(72155904,
  hashtext(source:sheet:dataset_key))` under `lock_timeout` 10 s, then 409
  `dataset_busy`. Separate from the Gold lock (7215590330), which the commit
  never takes.
- **What the person saw is what lands.** The body carries `validation_digest`
  (`domain/draft_commit.py`: spec digest, sheet, header row, rows, counts),
  `accept_warnings` and, for a catalogue, `base_run_id`. A stale draft
  (another spec digest, or no facts: validated before `0009`) is validated
  again first; any difference is a 409 and nothing is written.
- **The current version is Silver's `LATEST_RUNS` rule:** the latest completed
  run that landed the dataset (`manual_sheets` by `dataset_key`, the others by
  `sheet_name`). The difference is SQL: natural key trimmed (`btrim`, `''` for
  missing) plus its occurrence (`row_number`), a FULL JOIN, changed meaning
  another `content_hash`.
- **`silver.removed_rows`** (`kept` or `dropped`) is required for a snapshot
  and refused for a movement; `test_dataset_specs_committed.py` makes it
  `dropped` exactly when the Silver SQL has `DELETE FROM` or `TRUNCATE` of the
  spec's table. The texts are `datasets.<key>.removedRows`, else
  `loads.difference.removedRows.<value>`. What Silver should do with a line
  taken out of the catalogue is a question for the plant, under increment 11
  of the plan. **Do not change Silver for it without that answer.**
- **The facts of a load are read at validation** (`ExportFacts`: sheet names,
  the window from `DateSpan` through `landing.spanned`, the Info sheet, the
  search conditions) and kept on the draft, so the commit reads no workbook.
- **After the commit,** the staged rows are gone (deleted in the
  transaction); the draft and its issues stay 30 days. The purge's
  `landed(hash, bucket, key)` keeps an object only when `raw_files` points at
  that very bucket and key.
- **Known, and documented:** `raw_files.uploaded_at` is the commit's `now()`
  (the shared statement); an app run's `started_at` equals its `finished_at`;
  `parameters.validation.accepted` is `{by, warnings}`, the run's time being
  the time of the acceptance.
- **The history is Bronze's ledger,** command-line loads included.
  `LoadService._named` names a CLI load by its dataset when exactly one spec
  has its sheet. `GET /api/datasets` now reads the database for `last_load`,
  so a test of it binds an in-memory ledger.
- **`GET /api/loads/{run_id}/original`** streams through the API: `_verified`
  holds the last chunk back until the hash matches, so a damaged file arrives
  short. `Content-Disposition` carries `filename*` (RFC 6266).
- **In the app:** the commit is guarded by a ref and the disabled button;
  there is no "Cancelar"; a lost answer offers "Comprobar", which posts the
  same commit again. The history's filters live in the address; its dates
  are native `type="date"` inputs (`@mantine/dates` is not installed).
  `loads/LoadsTabs.tsx` is the section's sub-navigation (`aria-current`).
- **Tests:** `tests/fixtures/loads.py` holds `InMemoryBronze` (`add_cli_load`
  plants a command-line load), `InMemoryLoadCommitter` (`held_drafts`,
  `held_datasets`, and the `after_rows` hook that fails after the rows) and
  `InMemoryLoadLedger`. `InMemoryBronze.stamp()` makes its times strictly
  increase: Windows' clock moves in steps of about 15 ms, and two runs once
  had the same time. The test token verifier gives every token one subject,
  so "someone else's load" is a planted CLI load.
  `tests/integration/test_commit_end_to_end.py` proves the transaction
  against PostgreSQL and MinIO, a killed connection included.
- **Try commits on a scratch database,** never the development one: a
  committed synthetic catalogue becomes the current `lineas_produccion` at the
  next Silver build, and nothing voids a load until increment 11.

**Chat assistant** (phases 1 to 3 built 2026-10-19; [docs/chatbot-plan.md](docs/chatbot-plan.md)):

- **The imported package `src/energy/agent/` was dissolved into the layers**
  (`domain/chat.py`, `ports/chat_model.py`, `adapters/langchain_chat_model.py`,
  `services/chat_service.py`, `pipelines/flows/chat.py`); do not bring back a
  top-level `agent` package, which the architecture test does not know. Its
  WhatsApp, congress and conversation-table parts were dropped.
- **LangChain + OpenAI.** `langchain` and `langchain-openai` are main
  dependencies; `streamlit` is the extra `chat-ui`, for `scripts/chat_streamlit.py`
  only (`make chat-ui`, http://localhost:8501, on the host).
- **`CHAT_API_KEY`** (not the imported `API_KEY`) is a secret; empty means not
  configured and the platform still starts. `CHAT_MODEL`,
  `CHAT_CONTEXT_WINDOW_LIMIT`, `CHAT_TIMEOUT_SECONDS`, `CHAT_SYSTEM_PROMPT_FILE`.
- **The instructions are `adapters/prompts/system_prompt.txt`,** in English,
  packaged in the wheel; an empty or missing file is refused
  (`ChatNotConfiguredError`). They tell the model to answer in Spanish.
- **Stateless.** The caller sends the conversation with each question; the
  question is never in the history (the imported code sent it twice). Nothing
  of a conversation is logged.
- **ChatOpenAI takes its field names** (`model_name`, `openai_api_key`,
  `request_timeout`) under mypy's pydantic plugin, not the aliases.
- **Tests never call OpenAI:** a fake `ChatModel` and LangChain's
  `GenericFakeChatModel` (`tests/unit/test_chat.py`).
- **Phase 2 (built 2026-10-19): `CHAT_DATA_SOURCE=simulated` is the default
  and must stay so until the plant agrees to send rows to OpenAI** ([ADR
  0011](docs/adr/0011-the-assistant-reads-the-reporting-views.md), Proposed,
  amends ADR 0007). `simulated` is an in-memory DuckDB holding
  `adapters/simulated_reporting.py`: the 12 `reporting` views with the same
  names and column order (the integration test compares them with the real
  views; change both together), the equation's configuration restated, every
  row invented (`SIM-...`). `database` uses `PostgresReportingQuery` with the
  reporting login, never the application's.
- **The guards:** `domain/chat_sql.py` (one `SELECT`/`WITH`, literals and
  comments masked first), then the login's rights or DuckDB's locked
  configuration (no files, no extensions), a transaction always rolled back,
  a time limit (`CHAT_SQL_TIMEOUT_SECONDS`) and a row cap
  (`CHAT_SQL_ROW_LIMIT`). The PostgreSQL adapter sends the model's SQL on the
  raw psycopg cursor, so a `%` is never a parameter.
- **`config/chat/sources.yaml`** lists the plant's files as implemented (with
  views) or pending. Its note goes with every question
  (`ChatDataService.sources_note`): without it the model searched the EQ views
  for energy instead of saying the data is pending.
- **Two Spanish sentences are a deliberate exception** to the language rule:
  "No encontrado: la tabla está pendiente de implementación." in the English
  prompt, and "(Datos simulados: no son cifras reales de la planta.)"
  (`SIMULATED_NOTICE`), which `sources_note()` asks for in simulated mode
  only. Do not put the second back in the prompt: there, even conditioned on
  `data_source`, the model added it to answers on real data (2026-10-23).
  Tests keep both word for word.
- **The tools are plain methods** of `services/chat_data_service.py` handed to
  the adapter by the composition root, which wraps them with
  `StructuredTool.from_function` (name from the method, description from its
  docstring): the adapter imports no service.
- **Phase 3 (built 2026-10-19): `POST /api/chat`, permission `chat:use`**
  (viewer, operator, engineer, admin), and a panel on the dashboard.
  - **Two composition roots.** `api` may not import `pipelines`, so what both
    need is `adapters/chat_wiring.py` (`require_chat_key`,
    `build_reporting_query`, `build_chat_model`). `api/deps.py`
    `get_chat_service` builds the service on the first question and keeps it
    on `app.state`; a failure is never kept.
  - **Statuses:** 422 `empty_question`, 502 `chat_model_unavailable`, 503
    `chat_not_configured`, rows of `_STATUS_OF`. The question has no
    `min_length`, so a blank one is refused by the domain, in the envelope.
  - **History:** at most `MAX_CHAT_HISTORY` (200, `core/constants.py`)
    messages, each at most 20,000 characters; question at most 4,000. The
    browser keeps and sends at most 200 (`MAX_HISTORY` in
    `frontend/src/api/chat.ts`, kept equal by hand); the service trims to
    `CHAT_CONTEXT_WINDOW_LIMIT`.
  - **Compose passes the `CHAT_*` variables as `${VAR:-}`;** `ChatSettings`
    has `env_ignore_empty`, so an empty one keeps its default. Not passed:
    `CHAT_SYSTEM_PROMPT_FILE`, `CHAT_DB_HOST`, `CHAT_DB_PORT` (host settings).
  - **In the app:** `src/chat/` (`useChat`, `ChatLauncher`, `ChatLog`,
    `ChatComposer`, `chat.module.css`), shown on the dashboard when `GET /me`
    lists `chat:use`. The conversation is in `sessionStorage` under
    `clarios.chat` with the person's `subject`; a question shows at once and
    joins the history only with its answer; a failure puts it back in the
    box. Answers are plain text. `useDelayed` moved to `src/hooks/`.
  - **Mantine's growing `Textarea` reads `document.fonts`,** which jsdom
    lacks: `src/test/setup.ts` stubs it. The test fixtures `viewer` and
    `operator` carry `chat:use`, as the API gives it; `person()` is exported
    for a user without it.
  - **The launcher** (2026-10-20, replacing a full-width accordion) is a
    fixed round button bottom right; its panel grows out of it by CSS
    transitions on `data-open` (interruptible), and is `inert` and
    `aria-hidden` while closed, never unmounted, so the motion can close it.
    It is a parallel tool: no scrim, no close on a click outside. A dot on
    the button (and a live region beside it) marks an answer that landed
    while it was closed. Under 48 em the panel is a bottom sheet.
  - **After "Enviar" the focus goes back to the box:** the button turns
    disabled while the answer comes, and a disabled button drops the focus to
    the page, where Escape no longer reaches the panel.

**Energy baseline models** (built 2026-10-20; revision `0010`, [ADR 0012](docs/adr/0012-the-app-shows-the-energy-baseline-models.md)):

- **An exception to ADR 0007, and only this one.** `GET /api/models`,
  `GET /api/models/{model_key}` and `POST /api/models/{model_key}/forecast`
  serve figures, behind `models:view` (viewer, operator, engineer, admin).
  KPIs still go through Power BI.
- **Three models, no Ensamble** (its R² is too low): `orec_mensual` (last 36
  months), `fa_mensual` (all history), `fa_diario` (rectifiers, by day).
  `domain/baseline_models.py` (`SPECS`) holds them, with the forecast's
  arithmetic, the input checks and the defaults; nothing else may compute a
  forecast. `sin_eq` is derived from `eq`, never entered. `dias` is measured
  from 30 in the equation (`intercept_at_reference`); the fit centres it on
  its training mean, which changes no prediction.
- **Training runs on the host,** never in the api image: the energy is in the
  workbooks of `data/` (`ENERGY_DATA_DIR`), never loaded into the database, and
  the fit needs the `analytics` extra. `make models-train [MODEL=key]
  [DRY_RUN=1]` opens a relay to `db` on 127.0.0.1:5434 (`alpine/socat`) for the
  run and removes it, whatever happens; `make models-show` lists the models in
  force.
- **`adapters/sklearn_baseline_trainer.py` is the only module that imports
  scikit-learn or statsmodels,** and the flow imports it lazily. CI installs
  no extras, so `tests/unit/test_sklearn_baseline_trainer.py` is skipped
  there (`importorskip`), and so is the trainer's entry in the import sweep
  of `test_architecture.py` (`OPTIONAL_EXTRAS`): two more skips than
  locally. Diebold-Mariano with the
  Harvey correction, one step ahead, is computed as the one-sample t-test of
  the squared-error differences (statsmodels' `DescrStatsW`): scipy is not
  imported, because mypy has no override for it and CI does not have it.
- **The trainer is the notebook's:** a `ColumnTransformer` →
  `LinearRegression` pipeline, `TimeSeriesSplit(n_splits=24 or 30,
  test_size=1, max_train_size=window)`, HAC p-values from statsmodels on the
  pipeline's own columns (3 lags monthly, 7 daily), alert band 1.645 × the
  standard deviation of the validation errors. Training and validation
  figures are always kept apart. On 2026-10-20 the monthly models matched the
  notebook to the last digit (OREC 4.79 kWh/EQ, 4.89 % WAPE; F&A 16.78,
  4.72 %).
- **The daily model does not interpolate** the days without SCADA readings,
  which the notebook did (3 of 91): 15.24 kWh/EQ and 12.35 % WAPE instead of
  15.39 and 12.28 %. Do not add the interpolation back: it trains on
  invented days.
- **Daily production comes from Gold** (`PostgresDailyProduction`): the rule
  rows of `v_reporte_qad_regla` and `v_unit_ship_regla` times
  `silver.factores_equ`, FORMACION + DECORADO, every day from the first
  movement to the last, zero on a day without production. A test in
  `test_equs_gold_rules.py` keeps its months equal to `v_equs_batt_mes`.
- **The store supersedes, never overwrites;** a partial unique index keeps
  one model in force per key. A stored model whose variables differ from its
  spec in the code is not served: train again after changing a spec.
- **In the app** (`frontend/src/models/`): the chart is hand-drawn SVG, no
  chart library. The scenario's point and band move by CSS `transform` on
  `--ce-ease-spring` (attributes would jump). The forecast is asked 120 ms
  after the last change (`useDebouncedValue`), with `keepPreviousData`.
  Model and variable names are keys of `es.json` (`models.names`,
  `models.predicts`, `models.features`), looked up by `models/texts.ts`; a
  test of the API fails for a model or variable without its texts. The test
  fixtures `viewer` and `operator` carry `models:view`. A Mantine `Switch`
  with a `description` puts it in the accessible name: the description is
  a separate text with `aria-describedby`.
- **The test database container can need 90 s to start** on this machine
  with the stack up (2026-10-20). When the 120 s limit skips every database
  test, start a TimescaleDB container by hand with a published port and run
  pytest with `DATABASE_URL` pointing at it: the fixtures use a reachable
  `DATABASE_URL` instead of starting their own.

**EQUS Bronze loader** (built 2026-10-07):

- Layout follows the hexagon: payload rules in `domain/landing.py` and
  `domain/qad_export.py` (pure), ports in `ports/workbook_reader.py` and
  `ports/bronze_landing.py`, adapters in `adapters/calamine_workbook.py`,
  `postgres_bronze_repo.py`, `local_storage.py` and `minio_storage.py`, the use
  case in `services/bronze_loader.py`, the routing table and wiring in
  `pipelines/flows/equs_bronze_load.py`, the commands in `__main__.py`.
- **The payload rules are a contract.** How a header becomes a key and a cell
  becomes text decides every `content_hash`. Changing a rule makes the same
  source row hash differently from the copies already landed.
- A run is all-or-nothing: rows and the `completed` state commit together; on
  failure the rows roll back and the run is left `failed`. A sheet already landed
  by a *completed* run of the same file is skipped unless `--force`.
- **The loader knows nothing about fiscal years** (decided 2026-10-07: Silver
  keeps the row dates, Gold assigns the fiscal year). Do not add a year default
  or a year option to it. A run's `window_start` / `window_end` are the first
  and last *Fecha Efectiva* found among its rows (`DateSpan` in
  `domain/landing.py`), written with the completion. Only real date cells count;
  text is never parsed. The export's search conditions are stored as delivered
  and never used for the period, because the transactions export states a stale
  one. `energy equs load --window-start/--window-end` replaces the dates found;
  `load-all` has no such option.
- **Several exports side by side.** The two movement routes (`many=True`:
  transactions and invoices) land every workbook whose normalised name *starts
  with* the routed name, so `... por art FY24.xlsx` sits beside `... por art.xlsx`.
  Structures and catalogues are snapshots and match by exact name only. A
  workbook no route takes is reported (`LoadAllResult.unmatched`). No routed
  name may be the beginning of another; a unit test checks it.
- calamine's `iter_rows()` pads the empty rows above the data but drops the empty
  columns to its left, so a sheet's first row is always Excel row 1 while its
  first column may not be A. `tests/unit/test_calamine_workbook.py` pins this.
- The loader runs inside the api container (`make equs-load` mounts
  `data/raw_excels` read-only and archives to MinIO). From the host neither the
  database nor MinIO is reachable: point `DATABASE_URL` at a published database
  and pass `--archive-dir`.
- To try a load without touching the dev data, override on the make command
  line, which beats `.env`: `make equs-load POSTGRES_DB=<scratch db>
  MINIO_BUCKET_BRONZE=<scratch bucket>`.
- The acceptance tests (`tests/integration/test_equs_bronze_load.py`) need the
  workbooks in `data/raw_excels`, which is gitignored. Without them the module
  is skipped, so CI does not run it. They load with exact-name routes, so extra
  exports in that folder do not change the documented counts.
  `tests/integration/test_equs_other_years.py` builds its own small exports and
  runs everywhere.

**Tooling.**

- `make check` = format-check → lint → typecheck → security → frontend-check →
  test. It is exactly what CI runs (the frontend gate is its own CI job).
- **`README.md` is part of the images' dependency layer** (the root Dockerfile
  copies it with `pyproject.toml` and `uv.lock`). Any edit to the README makes
  the next `make up` reinstall every Python dependency in the api and dash
  images: 11 minutes on this machine on 2026-10-12, and over 20 when the disk
  is busy. So does any change to `pyproject.toml` or `uv.lock`. Make all such
  edits before one rebuild, and do it when the machine is idle: the second
  rebuild of 2026-10-12 took 37 minutes with about 2 GB of memory free, and the
  load made the development database stop and recover by itself (nothing was
  lost: its revision, its 42 tables and its run ledger were read back).
- **Line endings after a branch switch.** Git here has `core.autocrlf=true`,
  so a checkout writes the frontend's files with CRLF, while Biome writes
  LF and `biome ci` refuses CRLF. After switching branches, run
  `make frontend-format` before the gate. Git then lists the reformatted
  files as modified with an empty `git diff`; `git add` on those files (no
  content changes, nothing staged) clears the list. Seen on 2026-10-16,
  after a switch to `main` and back on 2026-10-15. At the same time
  `frontend/node_modules` was found half empty; `make frontend-install`
  restored it. The Python files are affected too: on 2026-10-17 `ruff format`
  rewrote 15 untouched modules of `src/energy` from CRLF to LF, cleared the
  same way.
- **Short of memory, tests fail for no reason of their own.** With under 2 GB
  free, Vitest's workers time out at start and test containers miss their 120
  seconds. Check the free memory before believing such a failure.
- Alembic is configured in `pyproject.toml` under `[tool.alembic]` — there is no
  `alembic.ini`. Always invoke as `alembic -c pyproject.toml`.
- `make db-upgrade` runs Alembic **inside the api container** because the
  database publishes no host port. Use `RUNNER=host` in CI or against a published
  port.
- The api image **copies** `migrations/` at build time (no bind mount). After
  editing a migration, rebuild the image (`docker compose build api` or
  `make up`) before `make db-upgrade`, or the container runs the old files.
- pip-audit runs against an exported requirements file (`--no-emit-project`);
  auditing the live environment fails because this package is not on PyPI.

---

## Deviations from the original Phase 1 specification

All deliberate; each is explained where it lives.

0. **Revisions `0003` and `0004` also create the EQUS Silver and Gold layers**
   (decided 2026-10-07 and 2026-10-08), beside the energy tables, rather than later
   revisions. Both were rewritten in place. A database whose `0004` predates EQUS
   Gold is migrated with `alembic downgrade 0003` and then `upgrade head`, which
   leaves Bronze and Silver alone (and drops `gold`, closed months included); one
   whose `0003` predates EQUS Silver goes down to `0002` instead. `0004` also
   inserts the four plant areas its sections point at into `silver.planta`, as the
   Silver build would.
1. **`reference` schema is created in `0003_create_silver_schema.py`.** Silver and
   Gold carry foreign keys into `plants`/`meters`, so it must exist first, and
   Silver is the first layer to need it. Renumbering the five specified
   migrations would have been worse. (It lived in `0002` until 2026-10-06, when
   `0002` was rewritten to create only Bronze for the EQUS work.)
2. **`dashboards/app.py` is implemented, not a placeholder.** The app *factory* is
   infrastructure wiring, exactly like `api/main.py` (which the spec marks
   `[implement]`), and the `dash` container must reach a healthy state for
   `docker compose up --wait` to succeed. It serves a holding page;
   `layouts.py`, `callbacks.py` and `components/charts.py` remain placeholders.
3. **`api/schemas/requests.py` and `responses.py` define real Pydantic models.**
   FastAPI introspects them at import time to build the OpenAPI schema, and a
   data contract's "implementation" *is* its fields. The handlers consuming them
   are the placeholders.
4. **Two extra test files.** `tests/unit/test_architecture.py` enforces the layer
   rule and drives the import sweep that makes the 70% coverage floor meaningful.
   `tests/unit/test_settings.py` holds regression tests for the Windows/MSYS
   pathname bug found during verification.
5. **Two extra migrations' worth of tables** beyond the minimum:
   `silver.consolidation_audit` and `bronze.raw_files`, inside the existing
   revisions. Both are needed for the audit chain the design promises.
6. **Placeholder endpoints return HTTP 501**, via a `NotImplementedError`
   exception handler in `api/main.py`, so an unbuilt endpoint reads as "not built
   yet" rather than "server bug".
7. **Analytics dependencies (scikit-learn, statsmodels, shap) are an optional
   extra**, not default dependencies — they are heavy and only Phase 5 needs them.
8. **`adapters/minio_storage.py` is implemented, and `__main__.py` partly**, ahead
   of Phases 3 and 2: the EQUS Bronze loader needs an archive and a command
   line. `__main__.py` registers only the `equs` commands; its other functions
   are still placeholders. `ensure_bucket` creates the bucket but sets no
   retention policy, which the original docstring had asked for.
9. **`api/security.py` is partly implemented ahead of Phase 3**, for the web
   app's sign-in (2026-10-12): the permission table, `Principal.has_role`,
   `require_roles` and the mapping from a token to a principal. Device API keys
   and filtering by plant are still Phase 3 placeholders. The `uploads` and
   `runs` placeholders, which people reached with a token, were removed in
   increment 7: `/api/drafts` and `/api/loads` replace them. `ingest` keeps the
   stand-in API key, for devices.

---

## Roadmap

- **Phase 1 — Foundations** ✅ complete (this scaffold)
- **Phase 2 — Domain model and KPI engine**: entities, value objects, time grid,
  equivalence factors, KPI formulas, CLI, seeding, synthetic data
- **Phase 3 — Ingestion adapters**: Excel/ERP/historian sources, MinIO and
  Postgres repositories, Pandera contracts, validate/consolidate/quarantine
  tasks, Prefect flows, watermarks, upload and ingest endpoints
- **Phase 4 — KPI computation and Gold**: KPI service, computation and roll-up,
  coverage accounting, Gold repository, and `reporting` views of the energy
  KPIs for Power BI. It starts by removing the KPI endpoints (ADR 0007, which
  lists the first steps). `README.md` still names "the KPI query endpoints" in
  its roadmap: correct it with that first change, not before, because a README
  edit forces a full reinstall in the images
- **Phase 5 — Analytics, alerting and reporting**: baseline regression, STL
  decomposition, anomaly and changepoint detection, SHAP attribution, notifiers,
  reports, full dashboards

The web app has its own roadmap, in section 8 of
[docs/frontend-plan.md](docs/frontend-plan.md): fourteen increments, of which 0
to 2 are done, 3 (the dashboard page, with the embedded Power BI report) is
built and waits for the owner's browser checks, 4 (the dataset specs) is
built, 5 (staging, upload and the checks of a file) is built and waits for
the owner's checks signed in, and 6 (the reasons and the corrected file) is
built and passed them on 2026-10-18. Increment 7 (the commit and the history)
is built and waits for the owner's checks signed in. Increment 9 (form and
grid entry) was removed on 2026-10-18 (ADR 0009); its number is kept.
Increment 8 is the checks against existing data.

When implementing a placeholder: read its docstring first (it is the spec), keep
and update the docstring, remove the `Implemented in: Phase N` line, and un-skip
the matching placeholder tests. One exception: `api/routers/kpis.py` is to be
removed, not implemented (ADR 0007).
