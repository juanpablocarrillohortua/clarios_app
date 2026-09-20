# 2. PostgreSQL 16 with TimescaleDB over a NoSQL time-series store

- **Status:** Accepted
- **Date:** 2026-09-18
- **Deciders:** Platform team
- **Supersedes:** —

## Context

The platform stores three quite different kinds of data:

1. **Time series.** Meter readings at up to 1-minute resolution across several
   plants. High volume, append-heavy, almost always queried by time range and
   meter.
2. **Relational dimensions.** Plants, meters, shifts, targets — low volume,
   heavily joined, with real referential constraints and temporal versioning.
3. **Computed aggregates.** KPI tables that must be transactionally replaced per
   period and read with sub-second latency by dashboards.

The obvious first instinct for (1) is a purpose-built time-series database —
InfluxDB, TimescaleDB, or a document store. But (2) and (3) are firmly
relational, and the platform's correctness guarantees lean on properties that
relational engines provide and most time-series stores do not.

Specifically, the design depends on:

- **Transactional multi-table writes.** Promoting a batch into Silver writes the
  accepted rows *and* the quarantine rows *and* the consolidation audit. Those
  must land together or not at all, or the invariant "rows in = rows landed +
  rows quarantined" is unverifiable.
- **Partial unique indexes.** `silver.readings` needs exactly one *live* row per
  `(meter_id, ts_utc)` while superseded corrections remain in the table. That is
  a unique index with a `WHERE` clause.
- **Check constraints.** "A `missing` reading carries no value" and "timestamps
  are grid-aligned" are enforced by the database, so even a direct `INSERT`
  cannot corrupt the layer everything downstream sums.
- **Foreign keys.** A reading must reference a real meter at a real plant.

A secondary but material consideration: this is a modular monolith run by a small
team. Every additional datastore is another thing to back up, monitor, upgrade,
secure, and reason about during an incident.

## Decision

We will use **PostgreSQL 16 with the TimescaleDB extension** as the single
database for every layer.

TimescaleDB is an extension, not a separate engine. That is the crux: hypertables
are partitioned tables that remain ordinary PostgreSQL tables. They accept
foreign keys, check constraints, partial indexes, and participate in normal
transactions, while gaining chunk exclusion, native compression, and retention
policies.

Concretely:

- `bronze.raw_readings`, `silver.readings`, `gold.kpi_hourly`,
  `gold.kpi_daily` and `gold.plant_daily_summary` are hypertables.
- Reference dimensions are ordinary tables.
- Raw payloads are stored in `JSONB`, so a source adding a field needs no
  migration — the schema flexibility usually cited in favour of a document store,
  available inside a relational engine.

## Consequences

**Easier:**

- One datastore to operate: one backup strategy, one monitoring setup, one
  upgrade path, one set of credentials. For a small team this dominates most
  other considerations.
- The correctness guarantees above are enforced by the engine rather than by
  application code that must be remembered and reviewed.
- A KPI query can join Gold against `reference.plants` and `reference.targets` in
  one statement. With a separate time-series store this becomes application-side
  joining, which is both slower and a place for bugs to live.
- Compression and retention are declarative policies rather than a cron job
  someone has to write and monitor.
- SQLAlchemy, Alembic, and pandas/Polars all speak PostgreSQL natively.

**Harder:**

- Hypertables impose a real constraint: **the partitioning column must appear in
  every unique constraint**, which is why the primary keys are composite
  (`(reading_id, ts_utc)` rather than `(reading_id)`). This surprises people and
  is documented in the data dictionary.
- Very high ingest rates would eventually need tuning that a purpose-built store
  handles by default. At the platform's projected volume — single-digit millions
  of rows per day — this is comfortably within PostgreSQL's range, but it is not
  free forever.
- TimescaleDB's API has evolved across the 2.x line (`create_hypertable` gained a
  new signature in 2.13, compression a new one in 2.18). Migration 0005 handles
  both forms rather than pinning to a single release.

**Accepted:**

- Compression and retention policies require the community edition. Migration
  0005 raises a warning and continues if they are unavailable, so the schema is
  still correct on a plain PostgreSQL — just larger.
- We are committed to PostgreSQL. Moving off it later would mean rewriting the
  migrations, the repositories, and several correctness guarantees.

## Alternatives considered

**InfluxDB.** Excellent at time-series ingest and retention. Rejected because the
relational half of the model has nowhere to live: no joins, no foreign keys, no
transactional multi-table writes. We would have run PostgreSQL *as well*, which
is the outcome this decision exists to avoid.

**MongoDB.** Attractive for the raw Bronze payloads, whose shape varies by
source. Rejected for the same reason plus a specific one: the Silver uniqueness
guarantee (one live row per meter-interval, with superseded rows retained) is a
partial unique index, which has no clean equivalent. `JSONB` gives us the schema
flexibility we actually needed without giving up the constraints.

**Plain PostgreSQL without TimescaleDB.** A reasonable fallback, and the schema
is deliberately designed to work without it. Rejected as the default because
manual partition management, hand-rolled retention, and no native compression are
all real operational burden for something an extension does declaratively.

**ClickHouse.** Very fast for analytical scans. Rejected because updates and
deletes are awkward, and Silver's supersede-rather-than-overwrite model needs
them. Its strengths are aimed at a query volume we do not have.
