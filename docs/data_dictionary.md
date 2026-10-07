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
| `silver` | Validated readings | Insert + supersede, never delete |
| `gold` | Published KPIs | Replaced per period |

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

## `silver` — validated readings

Created by [`0003_create_silver_schema.py`](../migrations/versions/0003_create_silver_schema.py).

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

---

## `gold` — published KPIs

Created by [`0004_create_gold_schema.py`](../migrations/versions/0004_create_gold_schema.py).
Written **only** by `pipelines/tasks/compute_kpis.py`.

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
