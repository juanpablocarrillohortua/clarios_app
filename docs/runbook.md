# Operations runbook

Written for whoever is on call. Every procedure assumes a shell on the Docker
host and the repository checked out.

**First response to almost anything:**

```bash
docker compose ps                    # what is running, what is healthy
curl -fsS localhost:${CADDY_HTTP_PORT:-8080}/api/ready | jq   # what the API thinks
docker compose logs --tail=200 api   # what it said before it broke
```

`/api/ready` names the failing dependency directly — connectivity, extensions, or
freshness — so start there rather than guessing.

---

## 1. Health and diagnosis

### What the endpoints mean

| Endpoint | Answers | Fails when |
|---|---|---|
| `GET /api/health` | "Is the process alive?" | The process is wedged. Touches nothing external — a database outage must **not** make this fail, or the orchestrator will restart healthy containers. |
| `GET /api/ready` | "Can it serve traffic?" | 503 when a required dependency is down. Returns 200 `degraded` when the database is fine but Silver is stale or empty. |

`degraded` is not an incident by itself. A freshly migrated database with no data
is degraded and correct.

### Reading a readiness failure

```json
{
  "status": "unhealthy",
  "checks": [
    {"name": "connectivity", "status": "unhealthy", "detail": "Database is not reachable"}
  ]
}
```

| Failing check | Meaning | Go to |
|---|---|---|
| `connectivity` | The API cannot reach PostgreSQL. | [§2 Database down](#2-database-down) |
| `extensions` | TimescaleDB or pgcrypto is missing. | [§3 Missing extension](#3-missing-timescaledb-extension) |
| `freshness` | Silver's newest row is older than `DATA_FRESHNESS_MAX_LAG_HOURS`. | [§4 Stale data](#4-stale-data) |

### Correlating logs

Every request carries an `X-Request-ID`, echoed on the response and bound to every
log line produced while handling it. When a user reports a problem, ask for that
id:

```bash
docker compose logs api | grep '<request-id>'
```

### Nobody can sign in, or the API answers 401 or 503

A request the API refuses with 401 never says why in its answer. The reason is
in the log:

```bash
docker compose logs api | grep "caller not authenticated"
```

A 503 with the code `auth_not_configured` means `ENTRA_TENANT_ID` or
`ENTRA_CLIENT_ID` is empty in `.env`. One with `identity_provider_unavailable`
means the API container cannot reach `login.microsoftonline.com`. The
troubleshooting table of [Sign-in and roles](sign-in.md#troubleshooting) lists
each reason with its fix.

### The dashboard shows a message in place of the report

The report is loaded by the browser from the Power BI Service, not by this
stack, so the API's log says nothing about it. The message itself names the
cause, and ends with Power BI's code and the ID of its request.
[What the messages mean](powerbi.md#what-the-messages-mean) lists each one with
its fix. The one this stack can cause is "report not configured":
`POWERBI_WORKSPACE_ID` or `POWERBI_REPORT_ID` is empty in `.env`, or the API was
not restarted after they changed.

---

## 2. Database down

**Symptoms:** `/api/ready` returns 503 with `connectivity` unhealthy; API logs
show connection errors.

```bash
# 1. Is the container running and healthy?
docker compose ps db

# 2. What did it say?
docker compose logs --tail=100 db

# 3. Can it answer at all?
docker compose exec db pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}"
```

**If the container is unhealthy or restarting**, the most common causes are:

| Cause | Check | Fix |
|---|---|---|
| Disk full | `df -h` | Free space, then restart. See [§7 Retention](#7-retention-and-disk-pressure). |
| Corrupt volume after an unclean shutdown | `docker compose logs db` shows recovery failure | Restore from backup ([§6](#6-backup-and-restore)). |
| Wrong data directory | Volume mounted at `/var/lib/postgresql/data` | The `-ha` image uses `/home/postgres/pgdata`. Fix `docker-compose.yml` and restore. |
| Out of memory | `docker stats`, `dmesg` | Raise the memory limit or reduce `DB_POOL_SIZE`. |

**Recovery:**

```bash
docker compose restart db
./scripts/wait_for_db.sh
curl -fsS localhost:${CADDY_HTTP_PORT:-8080}/api/ready
```

The API recovers on its own once the database returns — `pool_pre_ping` replaces
severed connections transparently. If it does not, restart `api` too.

---

## 3. Missing TimescaleDB extension

**Symptoms:** `/api/ready` reports `extensions` unhealthy; queries work but are
unexpectedly slow; `timescaledb_information.hypertables` is empty.

This is serious and quiet: the schema still accepts writes, but every
partitioning, compression and retention guarantee is silently gone.

```bash
# What is installed?
docker compose exec db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
  -c "SELECT extname, extversion FROM pg_extension;"

# Are the hypertables registered?
docker compose exec db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
  -c "SELECT hypertable_schema, hypertable_name FROM timescaledb_information.hypertables;"
```

**Expected:** `timescaledb` and `pgcrypto` present; five hypertables registered
(`raw_readings`, `readings`, `kpi_hourly`, `kpi_daily`, `plant_daily_summary`).

**If the extension is missing entirely**, the wrong image is running. Confirm:

```bash
docker compose config | grep -A2 'db:' | grep image   # must be timescale/timescaledb-ha:pg16
```

**If the extension is present but hypertables are not registered**, migration
0005 did not complete. Re-run it:

```bash
make db-upgrade
```

Migration 0005 is idempotent (`if_not_exists => TRUE` throughout) and safe to
re-run. Note that it raises a *warning* rather than failing when compression or
retention policies are unavailable — check the migration output for those
warnings if storage is growing faster than expected.

---

## 4. Stale data

**Symptoms:** `/api/ready` reports `freshness` degraded; a dashboard shows a flat
line ending some hours ago.

The first question is always **missing or zero?** A gap means data did not
arrive; a zero means the plant genuinely consumed nothing. The platform keeps
these distinct — `quality = 'missing'` with a null value — so check which it is:

```sql
SELECT quality, count(*), max(ts_utc)
FROM silver.readings
WHERE plant_id = (SELECT plant_id FROM reference.plants WHERE code = 'MTY-01')
  AND ts_utc > now() - interval '24 hours'
GROUP BY quality;
```

### Diagnosis path

```bash
# 1. Did the pipeline run? (a token of an operator or above, in a shell variable)
curl -fsS -H "Authorization: Bearer $TOKEN" "localhost:${CADDY_HTTP_PORT:-8080}/api/loads?limit=10" | jq

# 2. If a run failed, what happened?
curl -fsS -H "Authorization: Bearer $TOKEN" "localhost:${CADDY_HTTP_PORT:-8080}/api/loads/<run_id>" | jq '.state, .error'

# 3. If it succeeded but wrote nothing, is the source delivering?
curl -fsS "localhost:${CADDY_HTTP_PORT:-8080}/api/ingest/watermark/MTY-01/mty01_historian_main" | jq
```

| Finding | Cause | Action |
|---|---|---|
| No recent run | The Prefect schedule is not firing. | Check `prefect-server` and `prefect-worker` health; see [§5](#5-pipeline-failures). |
| Run failed with `SourceUnavailableError` | The source system is down or credentials expired. | Contact the plant; the platform will backfill on recovery. |
| Run succeeded, zero rows | The source is delivering nothing. | Check the watermark and the source's own logs. |
| Run succeeded, high quarantine count | The source format changed. | See [§8 Quarantine](#8-quarantine-backlog). |

### Re-running a period

```bash
# Re-run the nightly flow for a specific window
docker compose run --rm api python -m energy run-flow \
  --flow daily_etl --plant MTY-01 \
  --start 2026-09-17T00:00:00Z --end 2026-09-18T00:00:00Z
```

Safe to repeat: Bronze deduplicates on content hash, and Gold is replaced per
period rather than appended to.

---

## 5. Pipeline failures

```bash
docker compose ps prefect-server prefect-worker
docker compose logs --tail=100 prefect-worker
```

Open the Prefect UI at `http://localhost:${CADDY_HTTP_PORT:-8080}/prefect/`.

| Symptom | Likely cause | Action |
|---|---|---|
| Worker running, no runs picked up | The work pool name does not match. | Compare `PREFECT_WORK_POOL` in `.env` with the pool in the UI. |
| Worker restarting | Cannot reach the server. | Restart `prefect-server`, then `prefect-worker`. |
| Runs stuck `pending` | No worker is polling. | `docker compose restart prefect-worker`. |
| Run fails immediately | Configuration error. | `docker compose run --rm api python -m energy check-sources`. |

### Retrying a failed run

There is no retry endpoint: its placeholder was removed with increment 7 of the
web app ([ADR 0010](adr/0010-a-draft-is-committed-once-in-one-transaction.md)).

- A failed load of the command line is run again with the same command. It
  skips the sheets a completed run already landed from that file.
- A failed commit of the web app leaves no run at all, and the person confirms
  the draft again from its page.
- A flow run is run again for its window, as in "Re-running a period" above.

---

## 6. Backup and restore

### What must be backed up

| Volume | Contents | Criticality |
|---|---|---|
| `db_data` | Every medallion layer and all reference data | **Critical** — irreplaceable |
| `minio_data` | Original uploaded files | **Critical** — the audit trail's root |
| `prefect_data` | Run history | Useful; reconstructible |
| `caddy_data` | TLS certificates | Reacquired automatically |

Bronze and MinIO together are what make every number traceable. Losing either
breaks the audit chain even if Gold survives.

### Taking a backup

```bash
# Database - custom format, compressed, all schemas
docker compose exec -T db pg_dump \
  -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
  --format=custom --compress=9 \
  > "backup-$(date -u +%Y%m%dT%H%M%SZ).dump"

# Object storage
docker compose exec -T minio \
  mc mirror --overwrite local/bronze-raw /backup/bronze-raw
```

**Verify every backup.** An unverified backup is a hypothesis:

```bash
pg_restore --list backup-*.dump | head -20
```

### Restoring

```bash
# 1. Stop everything that writes
docker compose stop api dash prefect-worker

# 2. Recreate the database
docker compose exec -T db psql -U "${POSTGRES_USER}" -d postgres \
  -c "DROP DATABASE IF EXISTS ${POSTGRES_DB};" \
  -c "CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER};"

# 3. TimescaleDB must be present BEFORE restoring hypertables
docker compose exec -T db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
  -c "CREATE EXTENSION IF NOT EXISTS timescaledb;" \
  -c "SELECT timescaledb_pre_restore();"

# 4. Restore
docker compose exec -T db pg_restore \
  -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --no-owner < backup.dump

# 5. Re-enable TimescaleDB background workers
docker compose exec -T db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
  -c "SELECT timescaledb_post_restore();"

# 6. Bring migrations to head and restart
make db-upgrade
docker compose start api dash prefect-worker
curl -fsS localhost:${CADDY_HTTP_PORT:-8080}/api/ready | jq
```

> **The `timescaledb_pre_restore()` / `timescaledb_post_restore()` pair is not
> optional.** Restoring hypertable data without them corrupts TimescaleDB's
> internal catalog, and the damage is not obvious until a later query returns
> wrong results.

### Rebuilding derived layers

If Bronze survives but Silver or Gold is damaged, they can be regenerated — that
is the point of keeping Bronze immutable:

```bash
docker compose run --rm api python -m energy run-flow \
  --flow backfill --plant MTY-01 \
  --start 2026-01-01T00:00:00Z --end 2026-09-18T00:00:00Z
```

---

## 7. Retention and disk pressure

```bash
# Largest tables
docker compose exec db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -c "
SELECT hypertable_name,
       pg_size_pretty(hypertable_size(format('%I.%I', hypertable_schema, hypertable_name)::regclass)) AS size
FROM timescaledb_information.hypertables
ORDER BY hypertable_size(format('%I.%I', hypertable_schema, hypertable_name)::regclass) DESC;"

# Are the policies actually running?
docker compose exec db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -c "
SELECT job_id, proc_name, hypertable_name, next_start FROM timescaledb_information.jobs;"
```

Expected: a compression policy on `bronze.raw_readings`, `silver.readings` and
`gold.kpi_hourly`, plus a retention policy on `bronze.raw_readings` (2 years).

**If the jobs are missing**, migration 0005 warned and continued — the policies
could not be created. Re-run `make db-upgrade` and read its output.

**Emergency space recovery** (drops Bronze chunks older than one year — they are
raw evidence, so confirm the derived layers are settled first):

```sql
SELECT drop_chunks('bronze.raw_readings', older_than => INTERVAL '1 year');
```

---

## 8. Quarantine backlog

A rising quarantine rate means a source changed format. The rows are kept
deliberately — they are the evidence needed to fix the source.

```sql
-- What did one run reject?
SELECT rule, reason, raw_payload, quarantined_at
FROM silver.quarantine
WHERE run_id = '<run_id>'
ORDER BY quarantined_at;
```

```sql
-- What is failing, and why?
SELECT rule, source_system, count(*) AS rows, min(quarantined_at) AS since
FROM silver.quarantine
WHERE resolved_at IS NULL
GROUP BY rule, source_system
ORDER BY rows DESC;
```

The `raw_payload` column holds the original values and `bronze_row_id` points
back to the Bronze row, whose `source_uri` and `source_row` identify the exact
cell in the uploaded workbook.

**Typical causes:** a workbook template revision (fix `layout` in
`config/sources.yaml`), a unit change at the source, or a meter renamed without
updating `reference.meters`.

After fixing the source, re-ingest the period; corrected rows supersede the
originals rather than overwriting them.

---

## 9. Disagreement between sources

When a plant's spreadsheet disagrees with the dashboard, a source with higher
priority overrode theirs. That decision is recorded:

```sql
SELECT ts_utc, winning_source, winning_value,
       losing_sources, losing_values, divergence_pct
FROM silver.consolidation_audit
WHERE meter_id = '<meter-id>'
  AND ts_utc BETWEEN '<start>' AND '<end>'
ORDER BY divergence_pct DESC;
```

A `divergence_pct` above a few percent is not a resolved conflict — it means two
instruments are measuring different things, or one is misconfigured. Investigate
rather than accepting the winner.

Priorities live in `config/sources.yaml`; lower wins.

---

## 10. Routine maintenance

| Cadence | Task |
|---|---|
| Daily | Check `/api/ready`; check overnight run outcomes. |
| Weekly | Review the unresolved quarantine queue; check disk usage. |
| Weekly | Review the `Security` workflow results for new advisories. |
| Monthly | **Restore a backup into a scratch database and verify it.** |
| Monthly | Review `divergence_pct` outliers in the consolidation audit. |
| Quarterly | Review baseline model drift; re-fit where a changepoint was detected. |
| Quarterly | Review equivalence factors in `config/equivalent_units.yaml`. |

## 11. Escalation

Include in any escalation:

- the output of `curl -fsS .../api/ready`
- `docker compose ps`
- the `run_id` and/or `request_id` involved
- the affected plant codes and the UTC window
- relevant log lines (`docker compose logs --tail=200 <service>`)

## Related documents

- [Architecture](architecture.md) — how the layers fit together
- [Data dictionary](data_dictionary.md) — table and column reference
- [Sign-in and roles](sign-in.md) — giving someone access, and sign-in problems
- [README](../README.md) — setup and troubleshooting for developers
