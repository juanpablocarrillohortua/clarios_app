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
anything EQUS. Its Bronze layer is built (revision `0002`); its Silver and Gold
are not. [docs/database_overview.md](docs/database_overview.md) shows every schema
with diagrams.

---

## Build state as of 2026-09-19 - FULLY VERIFIED

`make check` exits 0 and `make up` brings all seven services to healthy. Every
Definition-of-Done item is verified against a running stack.

| Check | Result |
|---|---|
| `make up` | **Exit 0 - all 7 services healthy** (api, caddy, dash, db, minio, prefect-server, prefect-worker) |
| Port policy | Only caddy publishes (8080/8443); everything else internal |
| `make db-upgrade` | **Exit 0** - Alembic at head `0005` against live TimescaleDB |
| Schemas / tables | `bronze, gold, reference, silver`; 18 tables (8 + 3 + 4 + 3) |
| Extensions | timescaledb **2.30.1**, pgcrypto 1.3 |
| Hypertables | All 5 registered: `raw_readings, readings, kpi_hourly, kpi_daily, plant_daily_summary` |
| Timescale policies | 3 compression + 1 retention job created (no warnings) |
| Routes through Caddy | `/api/health` `/api/ready` `/api/docs` `/` `/prefect/api/health` all **HTTP 200** |
| `/api/ready` payload | `degraded` (connectivity+extensions healthy, Silver empty) - correct, returns 200 not 503 |
| Placeholder endpoint | `/api/kpis/summary` -> **501** with `code=not_implemented` |
| `X-Request-Id` | Present and propagated |
| `make check` | **Exit 0** - format, lint, mypy strict (82 files), bandit, pip-audit, tests |
| `make test` | **208 passed, 39 skipped** |
| Coverage | **93.74%** (threshold 70%) |
| `make test-integration` | **26 passed** against real TimescaleDB |
| No Spanish anywhere | 0 hits (UTF-8-aware scan, 2026-09-19). Since then Spanish appears only as data: header texts and sheet names of the QAD exports |

The table count and the three test rows were re-verified on 2026-10-06, after
revision `0002` was rewritten for the EQUS Bronze layer; the other rows date from
2026-09-19.

**Integration tests can be skipped silently.** They start a TimescaleDB container
and wait 120 s for it. On 2026-10-06, with the machine busy, the container needed
151 s, so `make check` exited 0 with every database test *skipped* (184 passed,
63 skipped). An idle re-run of `make test-integration` passed. Check the skip
count: 39 is normal, more means the database tests did not run.

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
