# Data dictionary

Field-by-field reference for every table. The authoritative definition is always
the migration that creates the table — this document explains *why* each column
exists and what it guarantees.

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

Created by [`0002_create_bronze_schema.py`](../migrations/versions/0002_create_bronze_schema.py).
It shares that migration because Silver and Gold carry foreign keys into it and
it must therefore exist before them.

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

Append-only. Nothing in the application updates or deletes a Bronze row; the only
thing that removes them is the retention policy (2 years) from migration 0005.

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

The ledger every Bronze row points at.

| Column | Type | Null | Description |
|---|---|---|---|
| `run_id` | `UUID` | no | Primary key. |
| `flow_name` | `VARCHAR(100)` | no | Which Prefect flow ran. |
| `source_system` | `VARCHAR(64)` | no | The source it read. |
| `plant_code` | `VARCHAR(32)` | no | The plant it ran for. |
| `state` | `VARCHAR(20)` | no | `pending`, `running`, `completed`, `failed`, `cancelled`. |
| `window_start` / `window_end` | `TIMESTAMPTZ` | yes | Period requested. Null for a full read. |
| `started_at` | `TIMESTAMPTZ` | no | Run start. |
| `finished_at` | `TIMESTAMPTZ` | yes | Null while running. |
| `row_count` | `BIGINT` | no | Rows written to Bronze. |
| `error` | `TEXT` | no | Failure message; empty on success. |
| `parameters` | `JSONB` | no | Invocation parameters — what a retry replays. |

### `bronze.raw_files`

Catalogue of archived source payloads, pointing at the immutable copy in MinIO.
This completes the audit chain: a Gold number resolves to Silver rows, which
resolve to Bronze rows, which resolve to a row here, which resolves to the exact
bytes a plant sent.

| Column | Type | Null | Description |
|---|---|---|---|
| `raw_file_id` | `UUID` | no | Primary key. |
| `content_hash` | `CHAR(64)` | no | **Unique** — a re-upload is detected before the file is parsed. |
| `bucket` / `object_key` | `VARCHAR(63)` / `TEXT` | no | Location in object storage. |
| `filename` | `TEXT` | no | Original filename as submitted. |
| `content_type` | `VARCHAR(150)` | no | MIME type. |
| `size_bytes` | `BIGINT` | no | Payload size. Must be positive. |
| `plant_code` / `source_key` | `VARCHAR` | no | What it was uploaded for. |
| `uploaded_by` | `VARCHAR(200)` | no | Who submitted it. |
| `uploaded_at` | `TIMESTAMPTZ` | no | Receipt time. |

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

Bronze is the only table with a retention policy. It is raw evidence whose value
decays once the derived layers are settled, and it is also by far the largest.

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
