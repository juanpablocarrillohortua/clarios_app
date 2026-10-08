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

---

## Build state as of 2026-09-19 - FULLY VERIFIED

`make check` exits 0 and `make up` brings all seven services to healthy. Every
Definition-of-Done item is verified against a running stack.

| Check | Result |
|---|---|
| `make up` | **Exit 0 - all 7 services healthy** (api, caddy, dash, db, minio, prefect-server, prefect-worker) |
| Port policy | Only caddy publishes (8080/8443); everything else internal |
| `make db-upgrade` | **Exit 0** - Alembic at head `0005` against live TimescaleDB |
| Schemas / tables | `bronze, gold, reference, silver`; 42 tables (8 + 9 + 4 + 21), 2 materialized views, 15 views and 8 functions in `gold` |
| Extensions | timescaledb **2.30.1**, pgcrypto 1.3 |
| Hypertables | All 5 registered: `raw_readings, readings, kpi_hourly, kpi_daily, plant_daily_summary` |
| Timescale policies | 3 compression + 1 retention job created (no warnings) |
| Routes through Caddy | `/api/health` `/api/ready` `/api/docs` `/` `/prefect/api/health` all **HTTP 200** |
| `/api/ready` payload | `degraded` (connectivity+extensions healthy, Silver empty) - correct, returns 200 not 503 |
| Placeholder endpoint | `/api/kpis/summary` -> **501** with `code=not_implemented` |
| `X-Request-Id` | Present and propagated |
| `make check` | **Exit 0** - format, lint, mypy strict (97 files), bandit, pip-audit, tests |
| `make test` | **525 passed, 39 skipped** (about 22 minutes with the workbooks present) |
| Coverage | **95.72%** (threshold 70%) |
| `make test-integration` | **118 passed** against real TimescaleDB and MinIO, with the workbooks present |
| No Spanish anywhere | 0 hits (UTF-8-aware scan, 2026-09-19). Since then Spanish appears only as data: header texts and sheet names of the QAD exports, and the table and column names of the EQUS Silver layer. One deliberate exception, asked for on 2026-10-08: `docs/equs_calculation_explained.es.md`, the Spanish translation of the non-technical explanation |

The table count and the `make check`, test and coverage rows were re-verified on
2026-10-07, after the EQUS Silver layer was built; the other rows date from
2026-09-19.

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
- The GitHub Actions workflows have not run on a real push.

## Environment specifics

| Item | Value |
|---|---|
| Repo root | `D:\Externado\Catch_2026\clarios_app` |
| uv | 0.12.17 at `C:\Users\juanp\.local\bin` — **not on the default PATH** |
| Python | 3.12.14, uv-managed (system Python is 3.13/3.14 — do not use it) |
| `.venv` | Holds notebook packages that are **not** in `uv.lock` (seaborn, matplotlib, ipykernel, jupyter). A plain `uv sync` is exact and would remove them. To add a dependency: edit `pyproject.toml`, `uv lock`, then `uv pip install <package>==<locked version>` |
| Shell for `make` | Git Bash at `D:\git\Git\bin\bash.exe`, resolved via `git --exec-path`. Selecting it as bare `bash.exe` picked up WSL's `C:\Windows\System32\bash.exe`, which has docker but no uv. `make` now works from PowerShell, cmd or Git Bash. |
| Published port | Only Caddy, `CADDY_HTTP_PORT=8080` |

---

## Conventions that are easy to get wrong

**Architecture.** `domain` and `core` import nothing from `energy`. `api`,
`dashboards` and `pipelines` are siblings and must never import one another.
Enforced by [`tests/unit/test_architecture.py`](tests/unit/test_architecture.py),
which parses imports with `ast`. **Do not weaken that test to make a change
pass** — it is load-bearing.

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

- `make check` = format-check → lint → typecheck → security → test. It is exactly
  what CI runs.
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

---

## Roadmap

- **Phase 1 — Foundations** ✅ complete (this scaffold)
- **Phase 2 — Domain model and KPI engine**: entities, value objects, time grid,
  equivalence factors, KPI formulas, CLI, seeding, synthetic data
- **Phase 3 — Ingestion adapters**: Excel/ERP/historian sources, MinIO and
  Postgres repositories, Pandera contracts, validate/consolidate/quarantine
  tasks, Prefect flows, watermarks, upload and ingest endpoints
- **Phase 4 — KPI computation and Gold**: KPI service, computation and roll-up,
  coverage accounting, Gold repository, KPI endpoints
- **Phase 5 — Analytics, alerting and reporting**: baseline regression, STL
  decomposition, anomaly and changepoint detection, SHAP attribution, notifiers,
  reports, full dashboards

When implementing a placeholder: read its docstring first (it is the spec), keep
and update the docstring, remove the `Implemented in: Phase N` line, and un-skip
the matching placeholder tests.
