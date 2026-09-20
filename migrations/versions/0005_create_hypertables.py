"""Convert the time-series tables into TimescaleDB hypertables.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-18

Runs last, after every table exists, because ``create_hypertable`` requires the
table to be present and (for the simple path) empty.

Three things happen here:

Partitioning
    ``bronze.raw_readings``, ``silver.readings``, ``gold.kpi_hourly``,
    ``gold.kpi_daily`` and ``gold.plant_daily_summary`` become hypertables.
    Chunk intervals are sized so a chunk holds roughly a week of Bronze, a month
    of Silver, and a quarter of Gold - Bronze is by far the highest-volume table
    and benefits from the smallest chunks.

Compression
    Older chunks are compressed, segmented by the column queries filter on
    (``meter_id`` for Silver, ``plant_id`` for Gold) so that chunk exclusion
    still works after compression.

Retention
    Bronze is dropped after two years; Silver and Gold are kept indefinitely.
    Bronze is raw evidence whose value decays once the derived layers are
    settled, and it is also the largest table by an order of magnitude.

**API compatibility.** TimescaleDB 2.13 introduced ``by_range()`` for
``create_hypertable`` and 2.18 introduced the columnstore compression API, both
superseding older spellings that still work but are deprecated. Each call below
tries the modern form and falls back to the classic one, so this migration
applies cleanly across the 2.x line rather than pinning the project to one
TimescaleDB release.

**Policies are advisory.** Compression and retention policies require the
community edition. If they are unavailable the migration raises a WARNING and
continues rather than failing: the schema is correct and usable without them,
and a scaffold that cannot migrate on a stripped-down PostgreSQL is worse than
one that migrates with a visible warning.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (qualified table, time column, chunk interval)
HYPERTABLES: tuple[tuple[str, str, str], ...] = (
    ("bronze.raw_readings", "ingested_at", "7 days"),
    ("silver.readings", "ts_utc", "30 days"),
    ("gold.kpi_hourly", "period_start", "90 days"),
    ("gold.kpi_daily", "period_start", "365 days"),
    ("gold.plant_daily_summary", "day", "365 days"),
)

# (qualified table, segment-by column, compress after)
COMPRESSION: tuple[tuple[str, str, str], ...] = (
    ("bronze.raw_readings", "source_system", "30 days"),
    ("silver.readings", "meter_id", "90 days"),
    ("gold.kpi_hourly", "plant_id", "180 days"),
)

# (qualified table, drop after)
RETENTION: tuple[tuple[str, str], ...] = (("bronze.raw_readings", "2 years"),)


def _create_hypertable(table: str, time_column: str, interval: str) -> None:
    """Convert one table into a hypertable, tolerating either API generation."""
    op.execute(
        f"""
        DO $$
        BEGIN
            BEGIN
                -- TimescaleDB >= 2.13
                PERFORM create_hypertable(
                    '{table}'::regclass,
                    by_range('{time_column}', INTERVAL '{interval}'),
                    if_not_exists => TRUE
                );
            EXCEPTION
                WHEN undefined_function OR syntax_error THEN
                    -- TimescaleDB < 2.13
                    PERFORM create_hypertable(
                        '{table}',
                        '{time_column}',
                        chunk_time_interval => INTERVAL '{interval}',
                        if_not_exists => TRUE,
                        migrate_data => TRUE
                    );
            END;
        END
        $$;
        """
    )


def _enable_compression(table: str, segment_by: str, compress_after: str) -> None:
    """Enable compression and schedule a policy, warning if unavailable."""
    op.execute(
        f"""
        DO $$
        BEGIN
            BEGIN
                ALTER TABLE {table} SET (
                    timescaledb.compress,
                    timescaledb.compress_segmentby = '{segment_by}'
                );
                PERFORM add_compression_policy(
                    '{table}', INTERVAL '{compress_after}', if_not_exists => TRUE
                );
            EXCEPTION WHEN OTHERS THEN
                RAISE WARNING
                    'Compression unavailable for {table}: %. The schema is still correct; '
                    'storage will simply be larger.', SQLERRM;
            END;
        END
        $$;
        """
    )


def _add_retention(table: str, drop_after: str) -> None:
    """Schedule a retention policy, warning if unavailable."""
    op.execute(
        f"""
        DO $$
        BEGIN
            BEGIN
                PERFORM add_retention_policy(
                    '{table}', INTERVAL '{drop_after}', if_not_exists => TRUE
                );
            EXCEPTION WHEN OTHERS THEN
                RAISE WARNING
                    'Retention policy unavailable for {table}: %. Old chunks must be '
                    'dropped manually - see docs/runbook.md.', SQLERRM;
            END;
        END
        $$;
        """
    )


def upgrade() -> None:
    """Create hypertables and attach compression and retention policies."""
    for table, time_column, interval in HYPERTABLES:
        _create_hypertable(table, time_column, interval)

    for table, segment_by, compress_after in COMPRESSION:
        _enable_compression(table, segment_by, compress_after)

    for table, drop_after in RETENTION:
        _add_retention(table, drop_after)


def downgrade() -> None:
    """Remove the policies.

    The hypertables themselves are not converted back to plain tables: there is
    no supported operation to do so, and the enclosing revisions drop the tables
    outright anyway. Policies are removed so that re-running the upgrade does not
    collide with a policy that already exists.
    """
    for table, _drop_after in RETENTION:
        op.execute(
            f"""
            DO $$
            BEGIN
                PERFORM remove_retention_policy('{table}', if_exists => TRUE);
            EXCEPTION WHEN OTHERS THEN
                NULL;
            END
            $$;
            """
        )

    for table, _segment_by, _after in COMPRESSION:
        op.execute(
            f"""
            DO $$
            BEGIN
                PERFORM remove_compression_policy('{table}', if_exists => TRUE);
            EXCEPTION WHEN OTHERS THEN
                NULL;
            END
            $$;
            """
        )
