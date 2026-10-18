# Data dictionary

Field-by-field reference for every table. The authoritative definition is always
the migration that creates the table — this document explains *why* each column
exists and what it guarantees. New to the project? Start with the
[database overview](database_overview.md), which shows how the tables relate.

**Conventions used throughout:**

- Every timestamp is `TIMESTAMPTZ` and stored in **UTC**. Local time is a
  presentation concern and is never stored, never used as a join key, and never
  used to bucket data.
- Silver and Gold timestamps are **interval starts** on the canonical hourly
  grid, not instants. A row timestamped `10:00` describes `[10:00, 11:00)`.
- Surrogate keys are `UUID` with a server-side `gen_random_uuid()` default, so a
  bulk `COPY` need not generate millions of keys client-side first.
- Hypertable primary keys are **composite and include the partitioning column**,
  because TimescaleDB requires it in every unique constraint.

| Schema | Purpose | Mutability |
|---|---|---|
| `reference` | Shared dimensions | Human-maintained, slowly changing |
| `bronze` | Raw landing area | Append-only, immutable |
| `silver` | Validated readings; EQUS tables | Readings: insert + supersede, never delete. EQUS: rebuilt from Bronze |
| `gold` | Published KPIs; EQUS Gold | KPIs: replaced per period. EQUS: views over Silver, plus the frozen monthly close, changed only by a reclose |
| `reporting` | Read-only views for BI tools such as Power BI | Views only; nothing is stored |
| `staging` | Files uploaded through the web app, while they are checked | A working area: a new check replaces the last; abandoned drafts are purged after 30 days |

---

## `reference` — shared dimensions

Created by [`0003_create_silver_schema.py`](../migrations/versions/0003_create_silver_schema.py).
It shares that migration because Silver and Gold carry foreign keys into it, and
Silver is the first layer to need it.

### `reference.plants`

One row per industrial site. The top-level aggregation unit: KPIs, targets and
baselines are all defined per plant.

| Column | Type | Null | Description |
|---|---|---|---|
| `plant_id` | `UUID` | no | Primary key. |
| `code` | `VARCHAR(32)` | no | Short unique code used in reports, e.g. `MTY-01`. |
| `name` | `VARCHAR(200)` | no | Full plant name. |
| `timezone` | `VARCHAR(64)` | no | IANA name, e.g. `America/Monterrey`. Used for shift alignment and report rendering **only** — never for storage. |
| `country` | `CHAR(2)` | yes | ISO 3166-1 alpha-2. |
| `floor_area_m2` | `NUMERIC(14,2)` | yes | Conditioned area; the denominator of energy intensity. Must be positive when present. |
| `commissioned_on` | `DATE` | yes | Service entry date. Readings before it are implausible. |
| `is_active` | `BOOLEAN` | no | Whether the plant is in scope for ingestion. Default `true`. |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | no | Row audit timestamps. |

**Constraints:** `code` unique; `floor_area_m2 > 0` when not null.

### `reference.meters`

One row per measuring point. A *virtual* meter is one whose readings are derived
(a sum of submeters, or a difference between a main and a branch) — the flag
exists so virtual meters are never double-counted in a plant total.

| Column | Type | Null | Description |
|---|---|---|---|
| `meter_id` | `UUID` | no | Primary key. |
| `plant_id` | `UUID` | no | FK → `reference.plants`, `ON DELETE CASCADE`. |
| `tag` | `VARCHAR(64)` | no | Source-system tag, unique within a plant. |
| `commodity` | `VARCHAR(20)` | no | One of `electricity`, `gas`, `steam`, `water`, `production`. |
| `unit` | `VARCHAR(16)` | no | Canonical unit of the readings (`kWh`, `Nm3`, `kW`). |
| `multiplier` | `NUMERIC(18,6)` | no | Factor from raw register value to `unit`. Must be positive. |
| `is_virtual` | `BOOLEAN` | no | Readings are derived rather than measured. |
| `parent_meter_id` | `UUID` | yes | Self-FK for submetering hierarchies, `ON DELETE SET NULL`. |
| `installed_on` / `removed_on` | `DATE` | yes | Service window. `removed_on` null while in service. |

**Constraints:** `(plant_id, tag)` unique; `commodity` restricted to the list
above; `multiplier > 0`.

### `reference.shifts`

Working periods used to slice KPIs by operating pattern. Defined in **plant-local
time** — a "night shift" means the same wall-clock hours all year — and projected
onto the UTC grid at query time, which is why the bounds are `TIME` rather than
`TIMESTAMPTZ`.

| Column | Type | Null | Description |
|---|---|---|---|
| `shift_id` | `UUID` | no | Primary key. |
| `plant_id` | `UUID` | no | FK → `reference.plants`. |
| `name` | `VARCHAR(50)` | no | Display name, e.g. `Night`. |
| `starts_at` | `TIME` | no | Local start, inclusive. |
| `ends_at` | `TIME` | no | Local end, exclusive. **May precede `starts_at`**, which means the shift crosses midnight. |
| `weekdays` | `SMALLINT[]` | no | ISO weekday numbers (1 = Monday) worked. |
| `valid_from` / `valid_to` | `DATE` | `valid_to` yes | Pattern validity. `valid_to` null means open-ended. |

### `reference.targets`

KPI targets, **temporally versioned**. A target agreed for 2026 must not
retroactively change how 2025 was judged, so historical performance is always
measured against the target that was in force at the time.

| Column | Type | Null | Description |
|---|---|---|---|
| `target_id` | `UUID` | no | Primary key. |
| `plant_id` | `UUID` | no | FK → `reference.plants`. |
| `kpi_key` | `VARCHAR(64)` | no | Must exist in `config/kpis.yaml`. |
| `value` | `NUMERIC(18,6)` | no | The target value. |
| `unit` | `VARCHAR(32)` | no | Unit the target is expressed in. |
| `valid_from` | `TIMESTAMPTZ` | no | First instant it applies. |
| `valid_to` | `TIMESTAMPTZ` | yes | First instant it stops applying. Null means open-ended. |
| `direction` | `VARCHAR(10)` | no | `lower` or `higher` — which way is better. Decides the sign of the reported variance. |

**Constraints:** `(plant_id, kpi_key, valid_from)` unique;
`valid_to > valid_from`; `direction` restricted.

---

## `bronze` — raw landing area

Created by [`0002_create_bronze_schema.py`](../migrations/versions/0002_create_bronze_schema.py).

Append-only. Nothing in the application updates or deletes a landing row. On the
five EQUS landing tables the database enforces it: a trigger rejects every
`UPDATE`. On `raw_readings` it is a convention, and the only thing that removes
its rows is the retention policy (2 years) from migration 0005.

Two tables are ledgers, not landing tables, and are updated on purpose:
`ingestion_runs` (the loader moves a run from `pending` to `completed`) and
`raw_files`.

### `bronze.raw_readings` — hypertable

The landing table for every ingested record. **Partitioned on `ingested_at`**,
7-day chunks, compressed after 30 days (segmented by `source_system`), dropped
after 2 years.

| Column | Type | Null | Description |
|---|---|---|---|
| `raw_reading_id` | `UUID` | no | Part of the composite PK. |
| `ingested_at` | `TIMESTAMPTZ` | no | When the row landed. **Partitioning column**, and part of the PK because TimescaleDB requires it. |
| `source_system` | `VARCHAR(64)` | no | Configured source key from `sources.yaml`. |
| `source_uri` | `TEXT` | no | Object-storage key or query reference locating the original payload. |
| `source_row` | `INTEGER` | no | 1-based row within the payload. This is what lets a rejected value be pointed at a specific spreadsheet cell. |
| `ingestion_run_id` | `UUID` | no | The run that wrote it. |
| `content_hash` | `CHAR(64)` | no | SHA-256 of the raw payload; the deduplication key. |
| `plant_code` | `VARCHAR(32)` | no | Denormalised for partition pruning on plant-scoped reads. |
| `payload` | `JSONB` | no | **The record exactly as read** — no coercion, no renaming, no unit conversion. A source adding a field needs no migration, and a value that fails to parse is still preserved for diagnosis. |

**Indexes:** `content_hash` (duplicate detection), `ingestion_run_id` (audit),
`(plant_code, source_system, ingested_at DESC)` (the common read).

**Constraints:** `source_row >= 0`; `content_hash` matches `^[0-9a-f]{64}$`.

### `bronze.ingestion_runs`

The ledger every Bronze row points at. The EQUS loader writes one row per load:
it inserts the run as `running` before landing anything, then sets it to
`completed` in the same transaction that lands the rows, or to `failed` after
rolling them back. So a `completed` run has all its rows and a `failed` run has
none.

| Column | Type | Null | Description |
|---|---|---|---|
| `run_id` | `UUID` | no | Primary key. |
| `flow_name` | `VARCHAR(100)` | no | The name the load ran under. The EQUS loader uses the workbook's file name without its extension unless told otherwise. |
| `source_key` | `VARCHAR(64)` | no | Which kind of export it loaded: `qad_rct_wo`, `qad_facturas`, `qad_product_struc`, `item_master` or `manual_sheets`. |
| `source_system` | `VARCHAR(64)` | no | The source it read: `qad` for exports of the ERP, `excel` for workbooks people maintain by hand. |
| `plant_code` | `VARCHAR(32)` | no | The plant it ran for. `1515` by default. |
| `state` | `VARCHAR(20)` | no | `pending`, `running`, `completed`, `failed`, `cancelled`. |
| `window_start` / `window_end` | `DATE` | yes | The business period the run covers: the earliest and the latest *Fecha Efectiva* among the rows it landed, written when the run completes. No year is assumed. `energy equs load --window-start/--window-end` replaces the dates found with the ones given. Null for product structures and catalogues, which are not dated, and for a movement export whose *Fecha Efectiva* holds no date cells. The export's own search conditions are **not** used: the transactions export states a stale period. |
| `qad_program` / `qad_exploration` / `qad_user` | `VARCHAR` | yes | What the workbook's *Info* sheet says about the query that produced it. |
| `queried_at` | `TIMESTAMPTZ` | yes | When the query ran, from *Fecha de consulta* on the *Info* sheet. The text is `dd/mm/yyyy h:mm:ss`, optionally followed by `a. m.` or `p. m.`, in America/Bogota time. Null when it cannot be read. |
| `search_conditions` | `JSONB` | no | The rows of the workbook's *Condiciones de búsqueda* sheet, as delivered: an array of objects keyed `campo`, `operador`, `valor`, `monto hasta`, with empty cells omitted. Empty array when the workbook has no such sheet. |
| `started_at` | `TIMESTAMPTZ` | no | Run start. |
| `finished_at` | `TIMESTAMPTZ` | yes | Null while running. |
| `row_count` | `BIGINT` | no | Rows written to Bronze. Never negative. |
| `error` | `TEXT` | no | Failure message; empty on success. |
| `parameters` | `JSONB` | no | Invocation parameters — what a retry replays. The EQUS loader stores `filename`, `content_hash`, `sheets` (name, dataset key and header row of each), `plant_code`, `force`, the period dates the caller gave (null when none), `uploaded_by`, and `info`: the whole *Info* sheet as a name-to-value object. |

**Indexes:** `(source_key, started_at DESC)`, `(plant_code, started_at DESC)`,
`state`.

**Constraints:** `state` and `source_key` restricted to the lists above;
`window_start <= window_end` when both are present.

### `bronze.raw_files`

Catalogue of archived source payloads, pointing at the immutable copy in MinIO.
This completes the audit chain: a Gold number resolves to Silver rows, which
resolve to Bronze rows, which resolve to a row here, which resolves to the exact
bytes a plant sent.

| Column | Type | Null | Description |
|---|---|---|---|
| `raw_file_id` | `UUID` | no | Primary key. |
| `content_hash` | `CHAR(64)` | no | **Unique** — a re-upload is detected before the file is parsed. |
| `bucket` / `object_key` | `VARCHAR(63)` / `TEXT` | no | Location in object storage. The key is `{plant_code}/{source_key}/{YYYY}/{MM}/{DD}/{content_hash}.{extension}`, dated with the UTC day the file was first archived. |
| `filename` | `TEXT` | no | Original filename as submitted. |
| `content_type` | `VARCHAR(150)` | no | MIME type. |
| `size_bytes` | `BIGINT` | no | Payload size. Must be positive. |
| `plant_code` / `source_key` | `VARCHAR` | no | What it was uploaded for. `source_key` takes the same five values as on `ingestion_runs`. One workbook can feed several sources; this is the first one it was loaded for. |
| `sheet_names` | `TEXT[]` | no | Every sheet found in the workbook, hidden ones included, whether or not it was landed. Empty by default. |
| `uploaded_by` | `VARCHAR(200)` | no | Who submitted it. |
| `uploaded_at` | `TIMESTAMPTZ` | no | Receipt time. |

**Indexes:** `(source_key, uploaded_at DESC)`.

### The EQUS landing tables

Five tables receive the exports the equivalent-batteries calculation consumes,
one spreadsheet row per table row. They are plain tables today; the composite
primary key lets a later revision turn them into hypertables.

| Table | Receives | Own indexes |
|---|---|---|
| `bronze.raw_rct_wo` | Production receipts: *Explorar transacciones por art* › `Datos` | payload keys `Número de transacción`, `Fecha Efectiva` |
| `bronze.raw_facturas` | Invoice lines: *Unidades facturadas* › `Datos` | payload keys `Núm Fact`, `Fecha Efectiva` |
| `bronze.raw_product_struc` | Product structures: *BOM BATERIAS FORMADAS* › `PASTA`, `PLACAS`, `BAT ENS`, `BAT FOR`, `BAT TERM`; *ESTRUCTURA DECORADO* › `Datos` | payload key `Artículo Matriz`; `(sheet_name, ingested_at DESC)` |
| `bronze.raw_item_master` | *Item_Master* › `Datos`, `Grupo-Fam` | `(sheet_name, ingested_at DESC)` |
| `bronze.raw_manual_sheets` | Hand-maintained catalogues, named by `dataset_key` | `(dataset_key, ingested_at DESC)` |

Every one of them has these columns:

| Column | Type | Null | Description |
|---|---|---|---|
| `<table>_id` | `UUID` | no | Part of the composite PK, e.g. `raw_rct_wo_id`. |
| `ingested_at` | `TIMESTAMPTZ` | no | When the row landed. Part of the PK so the table can become a hypertable. |
| `ingestion_run_id` | `UUID` | no | FK → `bronze.ingestion_runs`, `ON DELETE RESTRICT`. |
| `raw_file_id` | `UUID` | yes | FK → `bronze.raw_files`, `ON DELETE RESTRICT`. Null when the rows did not come from an archived workbook. |
| `source_system` | `VARCHAR(64)` | no | The system the export came from: `qad` or `excel`. |
| `source_uri` | `TEXT` | no | Where the original is archived and which sheet the row came from: `{bucket}://{object_key}#{sheet_name}`. |
| `sheet_name` | `VARCHAR(64)` | no | The worksheet the row was read from. |
| `source_row` | `INTEGER` | no | The real Excel row number, counting empty rows and the header. At least 1: some hand-made sheets have no header row. |
| `content_hash` | `CHAR(64)` | no | SHA-256 of the canonical JSON of the payload: keys sorted, compact separators, UTF-8, non-ASCII characters written as themselves. Indexed, **not unique**. |
| `plant_code` | `VARCHAR(32)` | no | The plant the export belongs to. |
| `payload` | `JSONB` | no | **The row exactly as exported.** Keys are the header texts of the sheet (Spanish, with accents); values are the cell text. Nothing is parsed. Always a JSON object. |

`raw_manual_sheets` adds `dataset_key` (`VARCHAR(64)`, lowercase snake_case),
which names the catalogue — `clasif_poly`, `lineas_produccion`, `instrucc`,
`element_deco`, `comparativo_lineas` — so a new catalogue needs a new value, not
a new table.

**Indexes on each:** `content_hash` (duplicate lookup), `ingestion_run_id` and
`raw_file_id` (audit).

#### What each table holds after loading the FY26 files

| Table | Sheet | `dataset_key` | Header row | Rows |
|---|---|---|---|---|
| `raw_rct_wo` | `Datos` | | 1 | 138,773 |
| `raw_facturas` | `Datos` | | 1 | 14,458 |
| `raw_product_struc` | `PASTA` | | 1 | 29 |
| `raw_product_struc` | `PLACAS` | | 1 | 220 |
| `raw_product_struc` | `BAT ENS` | | 1 | 8,340 |
| `raw_product_struc` | `BAT FOR` | | 1 | 4,566 |
| `raw_product_struc` | `BAT TERM` | | 1 | 3,413 |
| `raw_product_struc` | `Datos` (*ESTRUCTURA DECORADO*) | | 1 | 31,907 |
| `raw_manual_sheets` | `Clasif_Poly` | `clasif_poly` | 1 | 1,753 |
| `raw_manual_sheets` | `Lineas Produccion` | `lineas_produccion` | 1 | 64 |
| `raw_manual_sheets` | `INSTRUCC` | `instrucc` | none | 15 |
| `raw_manual_sheets` | `ELEMENT DECO` | `element_deco` | 1 | 1,751 |
| `raw_manual_sheets` | `Hoja1` (*Comparativo lineas*) | `comparativo_lineas` | 1 | 50 |
| `raw_item_master` | `Datos`, `Grupo-Fam` | | 1 | 0 — workbook not delivered yet |

`L.PRODUCCION` of *EQUS BATT* (`l_produccion`, header row 3) is landed only with
`energy equs load-all --include-optional`: it gives the group of each production
line, but shares its sheet with two pivot tables. The sheets `REPORTE QAD` and
`Unit_Ship` are refused by the loader, because their columns are results of the
Excel calculation and not sources.

#### How a row becomes a payload

The rules are implemented once, in
[`energy/domain/landing.py`](../src/energy/domain/landing.py), and Silver relies
on them to read Bronze.

**Keys.** A key is the header text of the column, trimmed. A blank header becomes
the Excel column letter (`A`, `B`, … `AA`). A repeated header gets ` #2`, ` #3`:
the QAD structure exports have two columns headed `Descripción`, which land as
`Descripción` and `Descripción #2`. When a sheet has no header row every key is a
column letter.

**Values.** Every value is text, written as follows.

| Cell | Text |
|---|---|
| Text | As it is. Not trimmed, not upper-cased; `N/A` stays `N/A`. |
| Whole number | No trailing `.0`: `900.0` → `900`. |
| Other number | Shortest exact decimal form, never scientific notation: `0.0636`, `1e-05` → `0.00001`. |
| Date, date-time, time | ISO-8601: `2025-12-10`, `2025-12-10T11:34:05`, `11:34:00`. |
| Boolean | `true` or `false`. |
| Empty | The key is omitted. |

**Rows.** A row that is completely empty is skipped, and so are the rows above
the header row. Every other row is landed, including exact repeats of an earlier
row and the header row pasted again inside `PLACAS` (Excel row 257).

#### How the tables are loaded

`energy equs load` lands one workbook and `energy equs load-all` (or
`make equs-load`) lands every workbook of the monthly close. For each load the
loader, in this order:

1. refuses a request it cannot honour (unknown source, missing or forbidden
   sheet, missing dataset key) before writing anything;
2. archives the file and registers it in `raw_files`, once per distinct hash;
3. skips the sheets a **completed** run already landed from this exact file, and
   stops if that is all of them — `--force` lands them again under a new run;
4. inserts the run as `running`;
5. lands all requested sheets in one transaction and, in that same transaction,
   sets the run to `completed` with the period its rows cover; on any error it
   rolls the rows back and sets the run to `failed`.

The loader only inserts into landing tables and only updates `ingestion_runs`.

**Any year, several files.** The loader does not know about fiscal years. It
lands whatever dates an export holds; the fiscal year of a figure is assigned
later, in Gold, from the date of each row.

| Kind of workbook | Files `load-all` lands | Why |
|---|---|---|
| Movements: *Explorar transacciones por art*, *Unidades facturadas* | Every workbook whose name **starts with** the usual name, for example `Calculo de equivalente - Explorar transacciones por art FY24.xlsx` | One export per period can sit side by side. Each is one load with its own run and its own period. |
| Product structures and catalogues | Only the workbook with exactly the usual name | They are snapshots: in Silver the latest load is the one that counts, so a second version must replace the file, not sit beside it. |

Names are compared ignoring case, spaces, hyphens and accents. A workbook that
no route takes is listed at the end of the output as not loaded. Two exports
that overlap land their shared rows twice, with the same `content_hash`;
removing the repeats is a Silver decision.

**Correcting a period.** A file that is already landed is skipped, so loading it
again with other dates changes nothing. `ingestion_runs` is a ledger and can be
updated: `UPDATE bronze.ingestion_runs SET window_start = ..., window_end = ...
WHERE run_id = ...`.

**Why `content_hash` is not unique.** The exports contain genuinely repeated
rows, and loading the same workbook twice is legitimate. Removing duplicates
changes the result of the calculation, so it is done in Silver, where it is
visible. Bronze keeps every copy.

**Append-only guard.** `bronze.forbid_update()` is attached to each landing
table as a `BEFORE UPDATE ... FOR EACH ROW` trigger and raises
`restrict_violation` naming the table. `DELETE` is not blocked, because that is
how a retention policy removes old rows.

---

## `silver` — validated readings and EQUS Silver

Created by [`0003_create_silver_schema.py`](../migrations/versions/0003_create_silver_schema.py).
Two families of tables live here and do not reference each other: the energy
readings, described first, and the tables of the equivalent-batteries
calculation, described under [The EQUS Silver tables](#the-equs-silver-tables).

### `silver.readings` — hypertable

Validated readings on the canonical hourly UTC grid. **Partitioned on `ts_utc`**,
30-day chunks, compressed after 90 days (segmented by `meter_id`).

| Column | Type | Null | Description |
|---|---|---|---|
| `reading_id` | `UUID` | no | Part of the composite PK. |
| `ts_utc` | `TIMESTAMPTZ` | no | **Interval start** on the canonical grid. Partitioning column. |
| `meter_id` | `UUID` | no | The meter. |
| `plant_id` | `UUID` | no | Denormalised so the common plant-and-period query needs no join. |
| `value` | `DOUBLE PRECISION` | **yes** | The measured quantity. Null **only** when `quality = 'missing'`. |
| `unit` | `VARCHAR(16)` | no | Canonical unit. |
| `quality` | `VARCHAR(20)` | no | `ok`, `interpolated`, `estimated`, `suspect`, `missing`. |
| `source_system` | `VARCHAR(64)` | no | Which source supplied the winning value. |
| `source_priority` | `SMALLINT` | no | The priority that source held, stored so a later higher-priority arrival can supersede this row without re-reading configuration. |
| `ingestion_run_id` | `UUID` | no | The run that wrote it. |
| `superseded_at` | `TIMESTAMPTZ` | yes | Null for the live row; set when a correction replaces it. |
| `created_at` | `TIMESTAMPTZ` | no | Write time. |

**The four constraints that make Silver trustworthy:**

| Constraint | What it prevents |
|---|---|
| `uq_silver_readings_live` — unique `(meter_id, ts_utc)` **where `superseded_at IS NULL`** | Two live values for the same interval. The partial predicate is what lets a correction coexist with the value it replaced. |
| `ck_readings_missing_has_no_value` | Storing "no data" as zero. A `missing` row must have a null value; any other flag must have one. |
| `ck_readings_grid_aligned` — `date_trunc('hour', ts_utc) = ts_utc` | An unaligned timestamp corrupting the grid, even via a direct `INSERT`. |
| `ck_readings_value_finite` | `NaN` or `Infinity` entering the layer everything downstream sums. |

### `silver.quarantine`

Rows rejected on the way into Silver. Sits beside `readings` as a peer, not a
footnote: rows in equals rows landed plus rows quarantined.

| Column | Type | Null | Description |
|---|---|---|---|
| `quarantine_id` | `UUID` | no | Primary key. |
| `run_id` | `UUID` | no | The run that rejected the row. |
| `bronze_row_id` | `UUID` | yes | Reference back to the offending Bronze row. |
| `plant_code` / `source_system` | `VARCHAR` | no | Where it came from. |
| `rule` | `VARCHAR(100)` | no | Name of the validation rule that failed. |
| `reason` | `TEXT` | no | Human-readable explanation, in English. |
| `raw_payload` | `JSONB` | no | The original values, so a steward sees exactly what arrived. |
| `quarantined_at` | `TIMESTAMPTZ` | no | Rejection time. |
| `resolved_at` | `TIMESTAMPTZ` | yes | Null while outstanding. |
| `resolution` | `TEXT` | no | What was done about it. |

**Indexes:** `run_id`, `rule`, and a partial index on
`(plant_code, quarantined_at DESC) WHERE resolved_at IS NULL` — the working queue
a data steward actually looks at.

### `silver.consolidation_audit`

How multi-source conflicts were resolved. Written whenever two or more sources
supply a value for the same meter and interval. Without it, a user whose
spreadsheet disagrees with the dashboard has no way to discover that a
higher-priority source overrode their number.

| Column | Type | Null | Description |
|---|---|---|---|
| `audit_id` | `UUID` | no | Primary key. |
| `ts_utc` | `TIMESTAMPTZ` | no | The contested interval. |
| `meter_id` | `UUID` | no | FK → `reference.meters`. |
| `winning_source` | `VARCHAR(64)` | no | Source whose value was kept. |
| `losing_sources` | `VARCHAR(64)[]` | no | Sources that were overridden. |
| `winning_value` | `DOUBLE PRECISION` | yes | The value that was kept. |
| `losing_values` | `DOUBLE PRECISION[]` | no | The values that were discarded. |
| `divergence_pct` | `DOUBLE PRECISION` | yes | Relative spread between candidates. A large value is a data-quality alert, not a resolved conflict. |
| `run_id` | `UUID` | no | The run that decided. |

**Indexes:** `(meter_id, ts_utc DESC)`, and a partial index on
`divergence_pct DESC WHERE divergence_pct > 2.0` surfacing material disagreements.

### The EQUS Silver tables

Eighteen tables and two materialized views: the typed, deduplicated version of
what the EQUS workbooks landed in Bronze, plus the equation stored as data.
[EQUS_CONTEXT.md](EQUS_CONTEXT.md) §6 is their specification and says which
Excel column each field comes from; this section says what each table
guarantees and how it is filled.

Conventions of these tables, which differ from the rest of the schema:

- **Names are Spanish.** Tables and columns mirror the Excel sheets and QAD
  headers the plant uses (`reporte_qad`, `campo_equs`, `articulo_matriz`).
- **Keys are natural.** An item code, a transaction number, an invoice line. No
  surrogate UUID.
- **Item codes are text.** `21500000A` and `21100000A` are real items.
- **Numbers are exact.** Every quantity, price and factor is `NUMERIC`, never a
  float: `36 x 0.0636` is `2.2896`, not `2.2895999`.
- **Rows are derived.** Every table except the three of the equation is filled
  by `make equs-silver` from Bronze. Do not edit them by hand; the next build
  overwrites the edit. The exceptions are the columns marked *kept by hand*.

#### Catalogues

| Table | Key | Columns | Notes |
|---|---|---|---|
| `planta` | `planta` | `nombre` | Plant areas (RC, Componentes, OREC, Ensamble, F&A). Not `reference.plants`, which are sites. `nombre` is kept by hand. |
| `almacen` | `almac` | `nombre` | Warehouses: 1010 plastic components, 1515 battery plant, 1520 and 1521 lead recycling. `nombre` is kept by hand. |
| `lineas_produccion` | `linea` | `descripcion`, `almac` → `almacen`, `planta` → `planta`, `grupo`, `proceso_energia`, `unidades_por_hora` | QAD production lines. `planta` is empty for a line no area claims. `grupo` is empty unless sheet `L.PRODUCCION` was landed. |
| `linea_de_productos` | `linea_de_productos` | `descripcion` | QAD product lines, which identify the process level: 2201 assembled, 3101 formed, 3103 decorated. |
| `campo_equs` | `campo_equs` | `descripcion` | EQ categories. **`N/A` is a row**: it means "not applicable". The plastic catalogue spells it `NA`; both are kept as delivered. `descripcion` is kept by hand. |
| `grupo_fam` | `familia_placa` | `tipo_bat`, `familia` | Battery families. Empty until `Item_Master.xlsx` is delivered. |
| `item_master` | `numero_articulo` | `descripcion`, `descripcion2`, `unidad_de_medida`, `linea_de_productos` → `linea_de_productos`, `campo_equs` → `campo_equs`, `tipo_de_articulo`, `grpo`, `familia_placa` → `grupo_fam`, `peso_neto`, `polaridad`, `estado` | One row per item code seen in any source. See [how it is assembled](#how-silver-is-built). |
| `costo_std` | `(numero_articulo, vigente_desde)` | `vigente_hasta`, `costo_std` | One row per change of the standard cost of an item. `vigente_hasta` is the day the next cost starts; empty for the cost in force. |
| `vendido_a` | `vendido_a` | `ordenar_nombre`, `pais`, `tipo` | Customers. A country written in capitals is stored in title case, so `COLOMBIA` and `Colombia` are one. |

`item_master.polaridad` is `+`, `-` or empty, and is **derived**, not exported:

| Item | Rule, in order |
|---|---|
| Paste (description starts with `PASTA`) | The type sheet `PASTA` gives it: `POSITVO` (as QAD spells it) is `+`, `NEGATIVO` is `-`. Otherwise `-` if the description contains `NEG`, else `+`. |
| Plate (description starts with `PLACA`) | `(+)` or `(-)` in the description. Otherwise its type. `PLACA PARA BAT IMPO` is `+`. |
| Anything else | Empty. |

The fictitious paste and plate of imported batteries count as positive because
the workbook's author put them in the positive column. Whether that is right is
an open question for the business.

#### Structures and catalogues of the calculation

**`bom_estructura`** — one row per parent-to-component link, every level in one
table. An item is the parent of some rows and the component of others, which
chains finished battery → formed battery → assembled battery → plate → paste.

| Column | Type | Null | Description |
|---|---|---|---|
| `bom_id` | `BIGINT` identity | no | Primary key. Restarted by every build. |
| `articulo_matriz` | `TEXT` | no | The parent. FK → `item_master`. |
| `articulo_componente` | `TEXT` | no | The component. FK → `item_master`. |
| `cantidad_por` | `NUMERIC(18,8)` | no | Quantity of the component per parent. Negative for recovered scrap. |
| `umcomp` | `TEXT` | yes | Unit of the component. |
| `inicio_efectiva` / `fin_efectivo` | `DATE` | yes | Validity. An empty `fin_efectivo` means the link is in force. |
| `desp`, `operacion`, `referencia`, `fecha_modificada`, `id_del_usuario` | | yes | As exported. |
| `calculo` | `VARCHAR(2)` | no | `SI` or `NO`: whether the row counts towards the paste of a formed battery. Only sheet `BAT FOR` says `NO`; a blank there, and every row of the other sheets, is `SI`. |
| `source_sheet` | `TEXT` | no | The Bronze sheet the row came from. |
| `source_run_id` | `UUID` | no | The Bronze run. FK → `bronze.ingestion_runs`. |

**Constraints:** unique on `(articulo_matriz, articulo_componente,
inicio_efectiva, fin_efectivo, cantidad_por)`, with empty dates treated as
equal. That is the business key of a link: a repeated link doubles whatever is
summed over it. **Indexes:** on the parent and on the component.

| Table | Key | Columns | Notes |
|---|---|---|---|
| `element_deco` | `numero_articulo` → `item_master` | `grpo`, `tipo_de_articulo`, `elemento_deco` (boolean) | The decoration catalogue: which components count as a decoration piece. |
| `clasif_poly` | `numero_articulo` → `item_master` | `campo_equs`, `cuenta_stats`, `factor_equ` | The factor of each plastic piece, for the separate EQUS POLY indicator. `TAPONES` is 0.05, as the workbook uses; a side table says 0.28, which is open. |
| `costo_conversion` | `(mes, planta, proceso)` | `costo_cop` | Conversion cost from accounting. Empty: no workbook carries it yet. |

#### Movements

**`reporte_qad`** — one row per production receipt. Key: `numero_de_transaccion`.

| Column | Type | Null | Description |
|---|---|---|---|
| `numero_de_transaccion` | `BIGINT` | no | Primary key: the QAD transaction number. |
| `fecha_efectiva` | `DATE` | no | The accounting date. **It decides the month**; `fecha`, the entry date, does not. |
| `numero_articulo` | `TEXT` | no | FK → `item_master`. |
| `almac` | `TEXT` | yes | FK → `almacen`. |
| `linea_produccion` | `TEXT` | yes | FK → `lineas_produccion`. Empty for the 2,518 receipts QAD records without a line. |
| `cambiar_cantidad_de_ubic` | `NUMERIC` | no | **The quantity used everywhere.** Negative for a reversal. |
| `campo_equs` | `TEXT` | yes | The category the item had when the export was taken. `N/A` is kept as text. |
| `precio`, `costo_total`, `peso_neto` | `NUMERIC` | yes | As exported. |
| `tipo_transaccion`, `fecha`, `tiempo`, `ubicacion`, `lote_serie`, `orden`, `id`, `unidad_de_medida`, `status_inventario`, `program`, `id_del_usuario` | | yes | As exported. |
| `source_run_id` | `UUID` | no | The Bronze run that brought this copy. FK → `bronze.ingestion_runs`. |

**Indexes:** `(fecha_efectiva, tipo_transaccion)`, `numero_articulo`,
`linea_produccion`.

**`unit_ship`** — one row per invoice line. Key: `(num_fact, orden, linea)`.

| Column | Type | Null | Description |
|---|---|---|---|
| `num_fact`, `orden`, `linea` | `TEXT`, `TEXT`, `INTEGER` | no | Primary key. |
| `fecha_efectiva` | `DATE` | no | **Decides the month.** |
| `vendido_a` | `TEXT` | yes | FK → `vendido_a`. |
| `numero_articulo` | `TEXT` | no | FK → `item_master`. |
| `cantidad_fac` | `NUMERIC` | no | Units invoiced. **Negative for a credit note**, so a sum is net. |
| `remision`, `canal`, `fecha_de_la_factura`, `embarcar_para`, `precio_de_lista`, `porcentaje_descuento`, `precio_neto`, `monto_factura`, `moneda`, `tipos_de_cambio`, `importe_de_factura_mb`, `total_plomo`, `campo_equs` | | yes | As exported. |
| `source_run_id` | `UUID` | no | FK → `bronze.ingestion_runs`. |

**Indexes:** `fecha_efectiva`, `numero_articulo`.

#### The equation

`EQ = quantity x (multiplicador / estandar_por_bat) x peso`. The three tables
below hold that equation as data. They are the only EQUS tables the build does
not write: version 1 is inserted by the migration, and a later version is added
by a person.

| Table | Key | Columns |
|---|---|---|
| `equs_version` | `version` | `nombre`, `vigente_desde`, `vigente_hasta`, `aprobado_por` |
| `factores_equ` | `(version, cuenta_stats)` | `proceso`, `descripcion`, `unidad`, `fuente`, `estandar_por_bat`, `multiplicador`, `peso`, `factor`, `suma_eq` |
| `instrucc` | `(version, cuenta_stats, regla_n)` | `linea_de_productos`, `campo_equs`, `campo_equs_excluir`, `prefijo_linea`, `signo` |

- **`factores_equ.factor` is generated** by the database as
  `multiplicador / estandar_por_bat * peso`. It cannot be written, so the factor
  and its three terms never disagree.
- **`fuente`** says what a driver sums: `PRODUCCION` (the quantity), `MAT_ACTIVO`
  (quantity x `td_form.pasta_pos`), `COMP_DECO` (quantity x `td_deco.comp_deco`)
  or `FACTURACION` (invoiced units).
- **`suma_eq`** is false for the four drivers that are reported but do not add
  to the total. They carry weight 0.
- **`instrucc`**: within one rule every condition that is not empty must hold;
  several rules of one driver add up.
- **To change a rule, add a version.** Editing version 1 changes every past
  month computed with it.

Version 1, `STD FY26`, in force from 2025-10-01:

| Driver | Process | Sums | Standard | Multiplier | Weight | Factor | Fed by |
|---|---|---|---|---|---|---|---|
| `OXPROD_KG` | OREC | kg | 6.08 | 1 | 0.074 | 0.012171 | category `OXIDO` |
| `GRIDS_REG_POS` | OREC | kg | 42 | 23.634 | 0.057 | 0.032075 | category `CINTA(+)` |
| `GRIDS_CAST` | OREC | units | 42 | 1.33 | 0.057 | 0.001805 | product line 2102 |
| `GRIDS_NEG` | OREC | kg | 42 | 25.28 | 0.037 | 0.022270 | category `CINTA(-)` |
| `PLATEPRD_SLI` | OREC | units | 84 | 1 | 0.178 | 0.002119 | category `PLACASLI` |
| `PLATEPRD_TRUCK` | OREC | units | 84 | 1.14 | 0.178 | 0.002416 | category `PLACA HD` |
| `COSPRODU_SLI` | ENSAMBLE | units | 1 | 1 | 0.354 | 0.354 | line 2201 except `TRUCK-B` |
| `COSPRODU_TRUCK` | ENSAMBLE | units | 1 | 2.15 | 0.354 | 0.7611 | line 2201 and `TRUCK-B` |
| `POS_ACT_MTL` | FORMACION | units x kg of paste | 3.39 | 1 | 0.146 | 0.043068 | line 3101 |
| `COMP_DEC` | DECORADO | units x pieces | 4.29 | 1 | 0.113 | 0.026340 | lines 3102, 3103, 3104 |
| `CARTON_BATT` | DECORADO | units | 4.29 | 12.8 | 0.113 | 0.337156 | no rule: contributes 0 |
| `UNITSHIP` | SHIPPING | invoiced units | 1 | 1 | 0.041 | 0.041 | lines 3103, 3104 |
| `15BATPRD` | INFO | units | 1 | 1 | 0 | 0 | no rule |
| `FORM_PRD_SLI` | INFO | units | 1 | 1 | 0 | 0 | line 3101 except `TRUCK-B` |
| `FORM_PRD_TRUCK` | INFO | units | 1 | 1 | 0 | 0 | line 3101 and `TRUCK-B` |
| `LEADPROD` | INFO | kg | 1 | 1 | 0 | 0 | production lines starting `RCREFI` |

The weights add up to 1 once each `(proceso, peso)` pair is counted once: OREC
0.346, ENSAMBLE 0.354, FORMACION 0.146, DECORADO 0.113, SHIPPING 0.041.

Left exactly as the workbook has them, because the business has not decided:
only `TRUCK-B` gets the 2.15 multiplier, not `TRUCK-S`; `CARTON_BATT` has a
factor and no rule; `UNITSHIP` is net of credit notes. Two gaps come from
EQUS_CONTEXT §6.5 itself: it gives `15BATPRD` no rule, and it says `UNITSHIP`
is "channel Venta" although `instrucc` has no column for the channel (the
invoices export is already restricted to that channel).

#### The two conversion tables

Materialized views, refreshed by the build. Each has a unique index on
`articulo_matriz`, so it can be refreshed while it is being read.

| View | Columns | Meaning |
|---|---|---|
| `td_form` | `articulo_matriz`, `pasta_pos`, `pasta_neg` | Kg of paste per formed battery (product line 3101). Only `pasta_pos` enters the calculation. Replaces pivot `TD_FORM`. |
| `td_deco` | `articulo_matriz`, `comp_deco` | Decoration pieces per semi-decorated, decorated or imported battery (3102, 3103, 3104). Replaces column X of sheet `Datos` and pivot `TD`. |

- **`td_form`** walks the structure upwards. Paste per plate: the links of a
  plate to a component whose description starts with `PASTA`, split by the
  polarity of the paste. Paste per assembled battery: plates per battery x paste
  per plate. Paste per formed battery: the sum over its links with
  `calculo = 'SI'`. Plates and assembled batteries are not filtered by product
  line, because the imported ones have none.
- **`td_deco`** sums `cantidad_por` over the in-force links of a battery whose
  component is a decoration element: not a `SOBRECUB` or `MANIJA` of line 1202;
  of line 1401, or described as `TAPA `, `CUBRE`, `POST PROTECTOR`, `TERM-PRT` or
  `TERMINAL-PRT`; and marked in `element_deco`.

With the FY26 workbooks: `td_form` has 1,248 rows and `td_deco` 4,889.

#### How Silver is built

`make equs-silver` (or `energy equs build-silver`) runs one transaction. If any
step fails, Silver is left as it was. Running it twice changes nothing.

**Which Bronze rows count.** Only rows of `completed` runs.

| Kind of source | Rows used | Silver |
|---|---|---|
| Snapshots: product structures, catalogues, item master | The latest completed run of each sheet or `dataset_key` | Replaced |
| Movements: receipts, invoice lines | Every completed run; the most recently landed copy of each natural key | Upserted, never deleted |

**How values are read.** Numbers with exact decimal semantics. Dates from the
date part of the ISO text. Codes and descriptions trimmed. `N/A` kept as `N/A`.
A cell that is not what its column should hold becomes empty rather than
stopping the build.

**What is conformed, in order:**

1. **Rows that are not links are left out**: a header row pasted among the data
   (its first cell holds the header text) and rows without a parent, a
   component or a quantity.
2. **A block below a pasted header is read by that header.** In `BAT FOR` the
   453 rows below row 4114 have type and group swapped and the unit where the
   sheet has the second description.
3. **Each link is kept once**, by its business key, from the newest run. A link
   that two sheets deliver (`BAT TERM` and `Datos` share 1,777) is kept once too.
4. **Production lines get one name.** Upper case, and `OEMPSOV` is `OEMPSOV1`.
5. **Every item code seen anywhere gets a row.** Each attribute takes the first
   value that is not empty, in this order: Item_Master (when delivered),
   receipts, invoices, structures as parent, structures as component, the
   decoration catalogue, the plastic catalogue. One exception: the product line
   of a component of sheet `Datos` comes from its column `LINEA DE PRODUCTO` and
   overrides the rest.
6. **Nothing is dropped to make a reference hold.** A code no source describes
   gets a row with the description `(sin descripción)` and is reported.

**The report.** Printed and returned by every build: rows per table; per
structure sheet, the rows left out, the repeats dropped and the links shared
with another sheet; and the checks that need a person.

| Check | With the FY26 workbooks |
|---|---|
| Items without description | None |
| Production lines without plant area | 14; three were used: `FADECO05`, `POLINY05`, `MAQBUJEO` |
| Plastic items whose factor is not their category's | 1 (`157000012`, category `PELLET`, which the factor table does not list) |
| Formed batteries produced without positive paste | None |
| Formed batteries whose paste adds up several assembled batteries | 1 (`231001483`) |
| Decorated batteries produced without decoration pieces | None |
| Items QAD marks `N/A` that the plastic catalogue classifies | 44 |

**Repeats dropped with the FY26 workbooks:** `PLACAS` 7, `BAT ENS` 13,
`BAT FOR` 11, `Datos` 70. Sheet `Datos` also has 741 rows that hold nothing but
a `1` in the first column (Excel rows 28,520 to 29,260); they are not links and
are left out.

**Known difference with the workbook.** `231001483` has two assembled
batteries: the link to the old one was closed in January 2026 and `BAT FOR`
marks only the new one with `CALCULO = SI`. A blank counts as `SI`, as
EQUS_CONTEXT specifies, so both add paste and `td_form` holds 5.3424 kg where
the workbook's pivot holds 2.6712. The documented target figures include it. It
is 116 EQ over FY26 and is reported by every build until someone decides.

---

## `gold` — published KPIs and EQUS Gold

Created by [`0004_create_gold_schema.py`](../migrations/versions/0004_create_gold_schema.py).
Two families live here: the energy KPI tables below, written **only** by
`pipelines/tasks/compute_kpis.py`, and [the EQUS Gold objects](#the-equs-gold-objects).

### `gold.kpi_hourly` — hypertable

Partitioned on `period_start`, 90-day chunks, compressed after 180 days.

| Column | Type | Null | Description |
|---|---|---|---|
| `kpi_hourly_id` | `UUID` | no | Part of the composite PK. |
| `period_start` | `TIMESTAMPTZ` | no | Interval start, hour-aligned. Partitioning column. |
| `plant_id` | `UUID` | no | FK → `reference.plants`. |
| `kpi_key` | `VARCHAR(64)` | no | Must exist in `config/kpis.yaml`. |
| `value` | `DOUBLE PRECISION` | no | The computed value. |
| `unit` | `VARCHAR(32)` | no | e.g. `kWh/unit`. |
| `coverage` | `DOUBLE PRECISION` | no | Fraction of the period backed by reliable readings, `[0, 1]`. |
| `computed_at` | `TIMESTAMPTZ` | no | When it was produced. |
| `run_id` | `UUID` | no | The run that produced it. |

**Unique:** `(plant_id, kpi_key, period_start)`.
**Constraints:** `coverage BETWEEN 0 AND 1`; value finite (an `Infinity` means a
KPI was computed with a zero denominator and should have been skipped);
`period_start` hour-aligned.

### `gold.kpi_daily`

Same shape plus `granularity` and `target_value`. Daily values are **recomputed
from Silver with production weighting**, not averaged from the hourly rows — the
unweighted mean of a ratio across hours of unequal output is the wrong number.

| Additional column | Type | Null | Description |
|---|---|---|---|
| `granularity` | `VARCHAR(10)` | no | `hour`, `day`, `week`, or `month`. |
| `target_value` | `DOUBLE PRECISION` | yes | Target in force for this period, denormalised so a variance renders without a temporal join. |

**Unique:** `(plant_id, kpi_key, granularity, period_start)`.

### `gold.plant_daily_summary` — hypertable

One denormalised row per plant per day; the table the dashboard landing page
reads. Deliberately wide so a summary view is one indexed lookup rather than a
pivot over the long KPI tables.

| Column | Type | Null | Description |
|---|---|---|---|
| `summary_id` | `UUID` | no | Part of the composite PK. |
| `day` | `TIMESTAMPTZ` | no | Midnight UTC. Partitioning column. |
| `plant_id` | `UUID` | no | FK → `reference.plants`. |
| `energy_kwh` | `DOUBLE PRECISION` | no | Total electricity. Non-negative. |
| `gas_kwh` | `DOUBLE PRECISION` | no | Total gas energy. Non-negative. |
| `production_equivalent_units` | `DOUBLE PRECISION` | no | Normalised output. |
| `sec_kwh_per_unit` | `DOUBLE PRECISION` | **yes** | **Null when production was zero** — an idle plant has no efficiency, which is a different statement from an efficiency of zero. |
| `load_factor` | `DOUBLE PRECISION` | yes | Constrained to `(0, 1]`. |
| `peak_demand_kw` | `DOUBLE PRECISION` | yes | Highest demand observed. |
| `baseline_kwh` | `DOUBLE PRECISION` | yes | Baseline prediction for the day. |
| `deviation_pct` | `DOUBLE PRECISION` | yes | Signed deviation. **Positive means worse** (consumed more than predicted). |
| `coverage` | `DOUBLE PRECISION` | no | `[0, 1]`. |

### The EQUS Gold objects

The Gold layer of the equivalent-batteries (EQ) calculation, also created by revision
0004. It **applies** the equation that Silver stores as data
(`silver.factores_equ`, `silver.instrucc`, `silver.equs_version`): the views filter
production and invoice rows with the rules, sum what each driver's `fuente` says,
and multiply by the factor of the version in force. No factor, weight, Campo Equs
value or product line is written in any view. The only literals are the
transaction type `RCT-WO`, the channel `Venta`, warehouse `1010`, the product-line
range `2101`–`3104` of data-quality check 1, and the four values of `fuente`. A
migration test reads the configuration and fails if any of its values appears in
a view.

Everything is a plain view or SQL function and reads Silver live. Nothing in Gold
is materialized: a whole fiscal year computes in under two seconds. The only
materialized objects the calculation depends on are `silver.td_form` and
`silver.td_deco`. The only tables are the layout of the report and the frozen
close.

Two companion pages:

- [equs_calculation_explained.md](equs_calculation_explained.md) walks from the
  exports to `v_resumen_equ` in plain language, following one receipt through
  every step.
- [equs_resumen_call_chain.md](equs_resumen_call_chain.md) traces `make resumen`
  through the code and shows how to call it from Python.

#### Layout tables (seeded by the migration)

| Table | Key | Columns | Holds |
|---|---|---|---|
| `secciones_reporte` | `proceso` | `planta`, `area_planta` (FK → `silver.planta`, nullable), `orden` | Column F of RESUMEN EQU. `OREC` → OREC; `ENSAMBLE` → ENSAMBLE; `FORMACION` and `DECORADO` → F&A; `SHIPPING` → DESPACHOS (no plant area); `INFO` → INFORMATIVOS; `POLY` → EQUS POLY, area `Componentes`. `proceso` is checked against the processes of `factores_equ` plus `POLY`: no table has the process as its key, so it cannot be a foreign key. |
| `filas_resumen_equ` | `cuenta_stats` | `fila` (unique) | The row of each driver (2–18) and plastic-injection account (20–30) in RESUMEN EQU, which is the order the report and `resumen-equ` print. |
| `controles_calidad` | `vista` | `descripcion`, `bloqueante`, `orden` | The data-quality views; checks 1–3 block a close. |

The migration also inserts the four plant areas the sections point at (`OREC`,
`Ensamble`, `F&A`, `Componentes`) into `silver.planta`, exactly as the Silver build
would, so the foreign key holds on a database that was never built. Downgrading
0004 removes them again while nothing in Silver points at them.

#### Calculation views

| View | One row per | Columns | Excel equivalent |
|---|---|---|---|
| `v_reporte_qad` | production receipt (`RCT-WO`) | every `silver.reporte_qad` column, plus `mes`, `linea_de_productos` (item master), `campo_equs_eq` (the receipt's Campo Equs, or the item master's only when the receipt has none; `N/A` is a value), `mat_activo` = quantity × `td_form.pasta_pos`, `comp_deco` = quantity × `td_deco.comp_deco`, and for warehouse 1010 only `new_equ` (category), `cuenta`, `factor_equ`, `valor_equ` = quantity × factor | Sheet REPORTE QAD with columns AZ, AY, BE, BG, BF |
| `v_reporte_qad_regla` | receipt × rule it matches, every version | `fecha_efectiva`, `mes`, the rule (`version`, `cuenta_stats`, `regla_n`, `signo`), `proceso`, `fuente`, `en_vigor`, `valor` (quantity, `mat_activo` or `comp_deco` as `fuente` says, times the sign) | The filters of the pivots that feed column D |
| `v_unit_ship_regla` | invoice line × rule it matches | the same, for `fuente = FACTURACION`: channel Venta in any case, product line and Campo Equs through the item master; a rule with `prefijo_linea` never matches an invoice. Credit notes subtract (shipping is net, open question 6). | Pivot TD_UNIT_Ship |
| `lineas_de_pdcto` | month × product line × Campo Equs | `descripcion`, `cantidad`, `mat_activo`, `comp_deco` | Pivot LINEAS DE PDCTO, per month |
| `v_cantidad_cuenta` | version × driver × month | `cantidad` (Q_k), `en_vigor` | Column D of RESUMEN EQU, for every version |
| `v_resumen_equ` | month × driver, version in force | `version`, `proceso`, `descripcion`, `cantidad`, `factor`, `equs` = cantidad × factor, `suma_eq` | **Column H of RESUMEN EQU** |
| `v_equs_batt_mes` | month | `equs_orec`, `equs_ensamble`, `equs_formacion`, `equs_decorado`, `equs_shipping`, `equs_batt` | Rows 36–45 |
| `inyeccion` | month × account × category | `piezas`, `valor_equ` (EQUS POLY, warehouse 1010; not added to EQUS BATT) | Pivot INYECCION, rows 20–34 |
| `v_costo_por_eq` | month × process | `equs`, `costo_cop` (summed over plant areas), `cop_por_eq` (null without cost or EQ) | New |

A driver with no rule, or whose rules matched nothing in the month, has no row in
`v_cantidad_cuenta` or `v_resumen_equ` (`GRIDS_REG_POS` and `CARTON_BATT` in
September 2026; `15BATPRD` always, because EQUS_CONTEXT gives it no rule). The
range functions below list every driver, with 0.

**The version in force.** `gold.en_vigor(vigente_desde, vigente_hasta, mes)` is the
only definition: a version is in force in a month when its validity contains the
month's first day. Every view and function asks it.

#### Range functions

All take `(p_desde date, p_hasta date)`, both included, and compare with
`fecha_efectiva`. They sum the same two pairing views as `v_cantidad_cuenta`, so a
whole month by range equals the month's view exactly.

| Function | Returns |
|---|---|
| `versiones_rango` | `(mes, version, nombre)` for each month the range touches; `version` null where none covers it, two rows where two overlap. |
| `resumen_equ_rango_mes` | `(mes, version, cuenta_stats, cantidad, equs)`: every driver of the version in force in each month, 0 when nothing matched. Each row uses the version of its own month. |
| `resumen_equ_rango` | `(version, cuenta_stats, cantidad, equs)`: the same, for the whole range. A range across a version change returns rows for both versions. |
| `resumen_equ_cierre_mes` | The frozen close in the shape of `resumen_equ_rango_mes`. |
| `inyeccion_rango_mes`, `inyeccion_rango` | `(mes, cuenta, categoria, area_planta, piezas, piezas_con_equ, factor_equ, equs)`: EQUS POLY. `piezas_con_equ` leaves out categories whose factor is 0 (the workbook's D32). |
| `calidad_datos_rango` | `(vista, descripcion, bloqueante, filas)`: the rows of each check inside the range. |

#### Data-quality views (EQUS_CONTEXT section 8)

Each returns the offending rows with their `fecha_efectiva` (check 6: `mes`), so they
can be counted for any range.

| View | Check | Blocks a close | FY26 | September 2026 |
|---|---|---|---|---|
| `dq_sin_regla` | Production of product lines 2101–3104 that no rule of the version in force takes | yes | 0 | 0 |
| `dq_doble_regla` | Receipts two or more rules of one process take | yes | 0 | 0 |
| `dq_sin_conversor` | Receipts of a `MAT_ACTIVO` driver without paste, or of a `COMP_DECO` driver without decoration pieces | yes | 34 | 0 |
| `dq_categoria_poly` | Receipts whose Campo Equs differs from the Clasif_Poly category of the item, for items whose category carries EQ | no | 1,588 | 141 |
| `dq_linea_sin_planta` | Receipts on production lines with no plant area | no | 1,429 (`FADECO05` 1,284, `POLINY05` 130, `MAQBUJEO` 15) | 0 |
| `dq_variacion` | Drivers whose monthly quantity moved more than the threshold against the previous month, or started or stopped | no | 73 | 7 |

- **Check 3** finds its drivers by `fuente`, not by product line. Its 34 FY26 rows are
  16 imported batteries (line 3104) whose structure gives 0 decoration pieces; none
  falls in September, so September closes without accepting warnings.
- **Check 4** compares the two columns and names no category. EQUS_CONTEXT
  section 7 counts "about 1,495" rows: those are exactly the covers and handles of
  lines 1200 and 1202. The view also finds the 93 receipts of `150000015`, truck
  handles (`ACCMED`, line 1305), whose category carries EQ just the same.
- **Check 6** reads its threshold from the session setting `equs.umbral_variacion`
  (a fraction; 0.30 when unset). The commands set it from `EQUS_VARIATION_THRESHOLD`.
- Check 7 of section 8 (structure links repeated by business key) is counted by the
  Silver build's report, per sheet, and has no Gold view.

#### The frozen close

| Table | Key | Columns |
|---|---|---|
| `resumen_equ` | `(mes, version, cuenta_stats)` | `cantidad`, `equs`, `fecha_calculo` (default `now()`), `closed_by`. FK `(version, cuenta_stats)` → `silver.factores_equ`, so a version a close used cannot be deleted. `mes` is a first day. |
| `cierre_mes` | `cierre_id` (identity) | `mes`, `cerrado_en`, `closed_by`, `recierre`, `advertencias_aceptadas`, `calidad_datos` (JSONB: the rows of each check the close saw). One row per close **and** reclose. |
| `resumen_equ_historial` | `(cierre_id, version, cuenta_stats)` | The rows a reclose replaced, as they were, under the `cierre_mes` row of that reclose. |

`energy equs close-month YYYY-MM` (`make close MONTH=…`):

1. takes the refresh lock and refreshes `td_form` and `td_deco`;
2. counts every data-quality check for the month and prints the counts;
3. refuses with exit 1 if a blocking check has rows, unless `--accept-warnings`, which
   is then recorded in `cierre_mes.advertencias_aceptadas`;
4. in one transaction: refuses if the month is closed and `--reclose` was not given;
   writes the `cierre_mes` row; on a reclose copies the old rows to
   `resumen_equ_historial`; upserts the month's `v_resumen_equ` rows into
   `resumen_equ` and deletes rows the new close no longer has;
5. prints the EQ of each process, EQUS BATT, EQUS POLY (live: the close does not
   freeze it) and the change against the previous closed month.

It also refuses a month with no production, or with no version or two versions in
force. **Nothing recomputes a closed month implicitly**: refreshes never touch these
tables.

#### The date-range report

`equs-report --start YYYY-MM-DD --end YYYY-MM-DD [--source live|closed] [--by-month]
[--output FILE] [--dsn URL]` (`make report START=… END=… [OUTPUT=…]`), or
`energy.pipelines.flows.equs_report.build_report(conn, start, end, source, by_month)`
from Python. It returns JSON laid out like RESUMEN EQU: `rango`, `fuente`,
`generado_en`, `versiones`, `plantas` → `procesos` → `subsecciones` (one per
cuenta STATS, with its row `fila`), `equs_batt`, `equs_poly`, `informativos`,
`calidad_datos`, `advertencias`; with `--by-month`, `por_mes` everywhere. The JSON
Schema is
[`equs_report.schema.json`](../src/energy/pipelines/flows/equs_report.schema.json).

- Every driver of the version(s) in force appears, even at 0. Across two versions a
  subsection's `factor` is null and `por_version` gives each.
- Totals are added from unrounded values. Quantities are rounded to 6 decimals, the
  terms of the equation to 10, EQ to 6; whole numbers are written as integers.
- `--source closed` reads `resumen_equ` and accepts only whole months that are all
  closed. EQUS POLY is still computed, with a warning.
- Warnings (`advertencias[].codigo`): `mes_parcial`, `varias_versiones`,
  `calidad_bloqueante`, `difiere_del_cierre` (a closed month's live EQ moved more than
  0.01 since its close), `poly_en_vivo`.
- Exit codes: 0 success; 1 invalid arguments or an unanswerable range (a month no
  version covers, the closed source over a partial or unclosed month); 2 database
  error.

#### Refreshing

`v_resumen_equ` reads Silver live. It is stale only when Silver is behind Bronze or a
conversion table is behind the structure. `energy equs refresh-gold [--with-silver]
[--month YYYY-MM] [--no-concurrently] [--format table|json] [--lock-timeout S]`
(`make refresh`; `make refresh-all` first lands `data/raw_excels`):

1. takes a session advisory lock (key 7215590330) under `lock_timeout`. A second
   refresh or close waits up to `EQUS_LOCK_TIMEOUT_SECONDS` (600) and then exits 3;
2. with `--with-silver`, builds Silver from Bronze in its own transaction;
3. refreshes `silver.td_form`, then `silver.td_deco`, each `CONCURRENTLY` in
   autocommit, so readers are never blocked (plain refresh when refused, or with
   `--no-concurrently`);
4. runs `ANALYZE` on the two views and the Silver tables the calculation reads;
5. prints the time of each step and column H of `--month`, or of the latest month with
   production.

`energy equs resumen-equ --month YYYY-MM [--format table|json|csv] [--source
live|closed]` (`make resumen MONTH=…`) prints that table on its own: one row per
cuenta STATS in the order of RESUMEN EQU with columns D, G and H, then EQUS BATT,
each plant, FORMACION and DECORADO, then the EQUS POLY categories and EQUS POLY.

Exit codes of the three commands: 0 success, 1 invalid arguments or a refused
request, 2 database error, 3 lock not released in time.

#### With the FY26 workbooks

September 2026 reproduces EQUS_CONTEXT section 9.1 to the cent: OREC 71,040.72,
assembly 85,014.78, formation 27,940.86, decoration 23,615.00, shipping 7,978.97,
**EQUS BATT 215,590.33**, **EQUS POLY 503,033.19** (1,737,504 pieces, 1,631,925 of
categories with EQ). Every month of section 9.2 matches to the cent, and FY26 is
2,310,724. Against column H of the workbook itself, only the rows section 9 explains
differ: POS_ACT_MTL (the plate listed twice in PLACAS), COMP_DEC (the TERM-PRT
protectors) and the totals that carry them (H36, H40, H44, H45).

---

## `reporting` — read-only views for BI tools

Created by [`0006_create_reporting_schema.py`](../migrations/versions/0006_create_reporting_schema.py).
How to connect Power BI to these views is in [powerbi.md](powerbi.md).

Twelve views and one function, no table. They restate EQUS Gold, and the Silver
tables Gold reads, as flat tables a BI tool can load and relate.

- **Nothing is calculated twice.** Every number comes from a Gold view.
  - `equs_mensual` is `gold.v_resumen_equ` with a zero for each driver nothing
    fed.
  - `aporte_cuenta` is the two Gold pairing views, row by row, so the detail adds
    up to the month exactly.
  - `equs_poly_mensual` and `calidad_datos_mensual` repeat the grouping of
    `gold.inyeccion_rango_mes` and `gold.calidad_datos_rango` over every month.
- **They read as their owner.** A view reads its tables with the privileges of
  the role that owns it, so the reader needs no privilege on `gold` or `silver`.
  For the same reason no view calls a Gold *function*: a function runs with the
  rights of whoever queries it.
- **Keys a BI tool can relate.** Power BI relates tables on one column, so every
  view keyed by a driver carries `cuenta_id` = `version:cuenta_stats` (for
  example `1:OXPROD_KG`).
- **Time columns.** Every month-based view has `mes`, the first day of the month,
  and `anio_fiscal` = `reporting.anio_fiscal(mes)`. The fiscal year runs from
  October to September and is named after the year it closes in: FY26 is
  October 2025 to September 2026.

| View | One row per | Columns | Source |
|---|---|---|---|
| `cuentas` | version × driver | `cuenta_id`, `version`, `version_nombre`, `vigente_desde`, `vigente_hasta`, `cuenta_stats`, `descripcion`, `proceso`, `planta`, `area_planta`, `orden_proceso`, `fila`, `unidad`, `fuente`, `estandar_por_bat`, `multiplicador`, `peso`, `factor`, `suma_eq` | `silver.factores_equ`, `equs_version`, `gold.secciones_reporte`, `filas_resumen_equ` |
| `articulos` | item | `numero_articulo`, `descripcion`, `descripcion2`, `unidad_de_medida`, `linea_de_productos`, `linea_de_productos_descripcion`, `campo_equs`, `tipo_de_articulo`, `grpo`, `familia_placa`, `peso_neto`, `estado` | `silver.item_master`, `linea_de_productos` |
| `equs_mensual` | month × driver of the version in force, 0 included | `mes`, `anio_fiscal`, `cuenta_id`, `version`, `cuenta_stats`, `descripcion`, `proceso`, `planta`, `area_planta`, `fila`, `unidad`, `cantidad`, `factor`, `equs`, `suma_eq` | `gold.v_resumen_equ`, for each month with receipts or invoices that a version covers |
| `equs_batt_mensual` | month | `mes`, `anio_fiscal`, `equs_orec`, `equs_ensamble`, `equs_formacion`, `equs_decorado`, `equs_shipping`, `equs_batt` | `gold.v_equs_batt_mes` |
| `equs_poly_mensual` | month × plastic account and category | `mes`, `anio_fiscal`, `cuenta`, `categoria`, `area_planta`, `fila`, `piezas`, `piezas_con_equ`, `factor_equ` (null when the category mixes factors), `equs` | `gold.v_reporte_qad`, warehouse 1010 |
| `aporte_cuenta` | receipt or invoice line × driver it fed, under the version in force | `origen` (`recibo` or `factura`), `numero_de_transaccion` (receipts only), `documento` (transaction number, or `num_fact/orden/linea`), `fecha_efectiva`, `mes`, `anio_fiscal`, `numero_articulo`, `cuenta_id`, `version`, `cuenta_stats`, `proceso`, `regla_n`, `valor`, `factor`, `equs` = `valor` × `factor`, `suma_eq` | `gold.v_reporte_qad_regla`, `gold.v_unit_ship_regla` |
| `recibos_produccion` | production receipt (`RCT-WO`) | `numero_de_transaccion`, `fecha_efectiva`, `mes`, `anio_fiscal`, `numero_articulo`, `articulo_descripcion`, `linea_de_productos`, `campo_equs`, `almac`, `linea_produccion`, `area_planta`, `cantidad`, `mat_activo`, `comp_deco`, `categoria_poly`, `cuenta_poly`, `factor_poly`, `equs_poly` | `gold.v_reporte_qad`, `silver.lineas_produccion` |
| `calidad_datos_mensual` | month × data-quality check, 0 included | `mes`, `anio_fiscal`, `vista`, `descripcion`, `bloqueante`, `filas` | the six `gold.dq_*` views, by `fecha_efectiva` (by `mes` for check 6) |
| `cierres` | close or reclose | `cierre_id`, `mes`, `anio_fiscal`, `cerrado_en`, `closed_by`, `recierre`, `advertencias_aceptadas`, `vigente` (the latest close of its month) | `gold.cierre_mes` |
| `cierres_calidad` | close × check | `cierre_id`, `mes`, `vista`, `descripcion`, `bloqueante`, `filas` | `gold.cierre_mes.calidad_datos` |
| `equs_cerrado` | closed month × driver | `mes`, `anio_fiscal`, `cuenta_id`, `version`, `cuenta_stats`, `descripcion`, `proceso`, `planta`, `area_planta`, `fila`, `unidad`, `cantidad`, `factor`, `equs`, `suma_eq`, `fecha_calculo`, `closed_by` | `gold.resumen_equ` |
| `equs_cerrado_historial` | row a reclose replaced | `cierre_id`, `mes`, `anio_fiscal`, `cuenta_id`, `version`, `cuenta_stats`, `proceso`, `cantidad`, `factor`, `equs`, `suma_eq`, `fecha_calculo`, `closed_by` | `gold.resumen_equ_historial` |

**Who can read them.** The group role `reporting_reader` (`NOLOGIN`) has `USAGE`
on the schema and `SELECT` on every view, and no privilege on any other schema.

- **The login.** `energy reporting create-reader` (or `make powerbi`) creates a
  login in that role, named by `REPORTING_DB_USER` (default `powerbi`), with the
  password `REPORTING_DB_PASSWORD`. Its sessions are read-only, it searches
  `reporting` first, and each query is stopped after 10 minutes. The command
  refuses to use a role that may do more than read.
- **Cluster-wide roles.** Roles belong to the whole PostgreSQL cluster, not to
  one database. So the migration creates the role only if it is missing, and the
  downgrade drops it only once no other database still grants it something.
- **A new view grants itself.** A revision that adds a view here must also grant
  `SELECT` on it to `reporting_reader`.

**With the FY26 workbooks.**

- **Figures:** `equs_mensual` gives every month of EQUS_CONTEXT section 9.2 to
  the cent; `aporte_cuenta` (134,833 rows) adds up to it exactly;
  `recibos_produccion` holds the 138,773 receipts.
- **Speed:** every view answers in under a second, except
  `calidad_datos_mensual`, which takes about 3 seconds.

---

## `staging` — uploads while they are checked

Created by [`0007_create_staging_schema.py`](../migrations/versions/0007_create_staging_schema.py).
The API's `/api/drafts` routes write it ([api.md](api.md)); what a file is
checked against is in [dataset-specs.md](dataset-specs.md#what-a-file-is-checked-against).

A working area, not a layer of the medallion: nothing here is evidence, no
foreign key points into Bronze, and nothing in Bronze points here. The schema
belongs to the application's role; `PUBLIC` and `reporting_reader` have no
privilege on it.

### `staging.drafts`

One upload of one `.xlsx` for one dataset spec.

| Column | Type | Null | Description |
|---|---|---|---|
| `draft_id` | `UUID` | no | Primary key. |
| `dataset_key` | `VARCHAR(64)` | no | The spec, `config/datasets/<key>.yaml`. Not a foreign key: specs are files. |
| `source_key` | `VARCHAR(64)` | no | The spec's Bronze source; one of the five EQUS keys. |
| `plant_code` | `VARCHAR(32)` | no | `1515`. |
| `input_mode` | `VARCHAR(10)` | no | `upload` today; `grid` and `form` arrive with later increments. |
| `status` | `VARCHAR(20)` | no | `receiving` while the bytes arrive, `uploaded` once the original is archived, `validated` once checked, `failed` when the upload broke off or was refused. |
| `filename` | `TEXT` | no | The name the person gave, cleaned: last path segment, NFC, no control characters, 1 to 255 characters. Data only: never a key or a path. |
| `content_type` | `VARCHAR(150)` | no | What the browser declared. Not trusted: the bytes are checked. |
| `declared_bytes` | `BIGINT` | no | The upload's `Content-Length`. |
| `size_bytes`, `content_hash` | `BIGINT`, `CHAR(64)` | yes | Size and SHA-256 of what arrived; null until then. |
| `bucket`, `object_key` | `VARCHAR(63)`, `TEXT` | yes | Where the original is archived: `bronze-raw`, `1515/<source_key>/<YYYY>/<MM>/<DD>/<sha256>.xlsx`, the key the Bronze loader uses for the same bytes. |
| `created_by`, `created_by_name` | `VARCHAR(64)`, `VARCHAR(200)` | no, yes | The person: Entra ID's subject (object ID), and their name for display. Only they see the draft. |
| `created_at`, `uploaded_at`, `validated_at` | `TIMESTAMPTZ` | no, yes, yes | When the upload began, when the original was archived, when it was last checked. |
| `expires_at` | `TIMESTAMPTZ` | no | When an abandoned draft is purged: 30 days after the last work on it (question 17 of the frontend plan). |
| `failure_code` | `VARCHAR(64)` | yes | Why a `failed` upload failed: `file_too_large`, `unsupported_file_format`, `incomplete_upload`, `client_disconnected`, `source_unavailable`. |
| `sheet_name`, `header_row`, `rows_read`, `rows_with_errors`, `rows_with_warnings` | | yes | What the last check found. |

**Constraints:** the statuses, input modes and source keys above; the hash
format; `uploaded` and `validated` drafts know their size, hash, bucket and key;
a `validated` draft knows when and how many rows; the row counts are consistent.
**Indexes:** `(created_by, created_at DESC)`, `expires_at`, `content_hash`.

### `staging.draft_rows`

The rows of a checked file exactly as Bronze would land them, from the same
rules (`energy.domain.landing`): `(draft_id, source_row)` as key,
`content_hash` (SHA-256 of the canonical payload) and `payload` (JSONB object).
Written by the check, for files the API checks itself (up to 5 MiB).

### `staging.draft_issues`

The first 1,000 issues of each rule in each column.

| Column | Type | Null | Description |
|---|---|---|---|
| `issue_id` | `BIGINT` | no | Identity. |
| `draft_id` | `UUID` | no | The draft; removing it removes its issues. |
| `rule_id` | `VARCHAR(40)` | no | The rule, for example `cell.type` ([the list](dataset-specs.md#what-a-file-is-checked-against)). |
| `severity` | `VARCHAR(10)` | no | `error`, `warning` or `info`. |
| `source_row` | `INTEGER` | yes | The Excel row; null for the file as a whole. |
| `column_index` | `INTEGER` | yes | The 0-based Excel column (0 is A); null when not in one column. |
| `column_key` | `TEXT` | yes | The column's payload key. |
| `value` | `TEXT` | yes | The text found, at most 200 characters. |
| `params` | `JSONB` | no | What the message needs: the pattern, the allowed values, the bounds. |

**Indexes:** `(draft_id, source_row, column_index)` for the list in order, and
`(draft_id, severity, rule_id)` for its filters.

### `staging.draft_rule_counts`

Every issue, counted: `(draft_id, rule_id, column_key)` as key (`column_key`
empty for the file as a whole), `severity`, `rows_affected` (every row that
broke the rule) and `issues_listed` (how many of them `draft_issues` holds).

---

## TimescaleDB configuration

Applied by [`0005_create_hypertables.py`](../migrations/versions/0005_create_hypertables.py).

| Table | Time column | Chunk | Compress after | Segment by | Retention |
|---|---|---|---|---|---|
| `bronze.raw_readings` | `ingested_at` | 7 days | 30 days | `source_system` | 2 years |
| `silver.readings` | `ts_utc` | 30 days | 90 days | `meter_id` | indefinite |
| `gold.kpi_hourly` | `period_start` | 90 days | 180 days | `plant_id` | indefinite |
| `gold.kpi_daily` | `period_start` | 365 days | — | — | indefinite |
| `gold.plant_daily_summary` | `day` | 365 days | — | — | indefinite |

Chunk sizes descend with volume: Bronze is the highest-volume table by an order
of magnitude and benefits from the smallest chunks. `segment_by` is set to the
column queries filter on, so chunk exclusion still works after compression.

`bronze.raw_readings` is the only table with a retention policy. It is raw
evidence whose value decays once the derived layers are settled, and it is also
by far the largest. The EQUS landing tables are not hypertables yet and have no
policy.

---

## Cross-layer invariants

These hold across the whole platform and are what the tests and constraints exist
to protect:

1. **Every Bronze row is traceable** to the bytes that produced it, via
   `source_uri` + `content_hash` + `source_row`.
2. **Rows in = rows landed + rows quarantined.** Nothing is silently dropped
   between Bronze and Silver.
3. **Missing ≠ zero**, at every layer, enforced by constraint in Silver and by
   null `sec_kwh_per_unit` in Gold.
4. **Every Gold value carries its coverage.** No number is published without the
   context needed to judge it.
5. **Every timestamp is UTC and grid-aligned**, enforced by check constraints
   rather than convention.
6. **Corrections supersede, never overwrite.** History remains reconstructible.
