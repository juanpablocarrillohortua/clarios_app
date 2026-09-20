"""Create the Gold schema: the KPI tables dashboards and the API read.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-18

Gold is a published layer. It is rewritten per period by the KPI pipeline rather
than mutated row by row, so every row records the ``run_id`` that produced it
and the ``computed_at`` instant - a figure in a report can always be traced to
the run that computed it.

The column that makes this schema trustworthy is ``coverage``: the fraction of
each period actually backed by reliable Silver readings. It is ``NOT NULL`` with
a ``[0, 1]`` check on every table here. A KPI without its coverage is a number
without an error bar, and consumers will read it as certain.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REFERENCE = "reference"
GOLD = "gold"

GRANULARITIES = ("hour", "day", "week", "month")


def upgrade() -> None:
    """Create the Gold schema and its tables."""
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {GOLD}")

    granularity_list = ", ".join(f"'{g}'" for g in GRANULARITIES)

    # ------------------------------------------------------------------
    # gold.kpi_hourly - hypertable in revision 0005
    # ------------------------------------------------------------------
    op.create_table(
        "kpi_hourly",
        sa.Column(
            "kpi_hourly_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kpi_key", sa.String(64), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("coverage", sa.Double(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("kpi_hourly_id", "period_start", name="pk_kpi_hourly"),
        sa.CheckConstraint("coverage >= 0 AND coverage <= 1", name="ck_kpi_hourly_coverage"),
        # Infinity means a KPI was computed with a zero denominator and should
        # have been skipped instead of stored.
        sa.CheckConstraint(
            "value = value AND value <> 'Infinity'::double precision "
            "AND value <> '-Infinity'::double precision",
            name="ck_kpi_hourly_value_finite",
        ),
        sa.CheckConstraint(
            "date_trunc('hour', period_start) = period_start", name="ck_kpi_hourly_aligned"
        ),
        schema=GOLD,
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_kpi_hourly_scope
            ON {GOLD}.kpi_hourly (plant_id, kpi_key, period_start)
        """
    )

    # ------------------------------------------------------------------
    # gold.kpi_daily
    # ------------------------------------------------------------------
    op.create_table(
        "kpi_daily",
        sa.Column(
            "kpi_daily_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kpi_key", sa.String(64), nullable=False),
        sa.Column("granularity", sa.String(10), nullable=False, server_default="day"),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("coverage", sa.Double(), nullable=False),
        # Denormalised so a variance can be rendered without a temporal join
        # back to reference.targets.
        sa.Column("target_value", sa.Double(), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("kpi_daily_id", "period_start", name="pk_kpi_daily"),
        sa.CheckConstraint("coverage >= 0 AND coverage <= 1", name="ck_kpi_daily_coverage"),
        sa.CheckConstraint(f"granularity IN ({granularity_list})", name="ck_kpi_daily_granularity"),
        sa.CheckConstraint(
            "value = value AND value <> 'Infinity'::double precision "
            "AND value <> '-Infinity'::double precision",
            name="ck_kpi_daily_value_finite",
        ),
        schema=GOLD,
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_kpi_daily_scope
            ON {GOLD}.kpi_daily (plant_id, kpi_key, granularity, period_start)
        """
    )

    # ------------------------------------------------------------------
    # gold.plant_daily_summary - the wide table the landing page reads
    # ------------------------------------------------------------------
    op.create_table(
        "plant_daily_summary",
        sa.Column(
            "summary_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("day", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("energy_kwh", sa.Double(), nullable=False, server_default=sa.text("0")),
        sa.Column("gas_kwh", sa.Double(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "production_equivalent_units",
            sa.Double(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # Null rather than zero when production was zero: an idle plant has no
        # efficiency, which is a different statement from an efficiency of zero.
        sa.Column("sec_kwh_per_unit", sa.Double(), nullable=True),
        sa.Column("load_factor", sa.Double(), nullable=True),
        sa.Column("peak_demand_kw", sa.Double(), nullable=True),
        sa.Column("baseline_kwh", sa.Double(), nullable=True),
        sa.Column("deviation_pct", sa.Double(), nullable=True),
        sa.Column("coverage", sa.Double(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("summary_id", "day", name="pk_plant_daily_summary"),
        sa.CheckConstraint("coverage >= 0 AND coverage <= 1", name="ck_summary_coverage"),
        sa.CheckConstraint("energy_kwh >= 0", name="ck_summary_energy_non_negative"),
        sa.CheckConstraint("gas_kwh >= 0", name="ck_summary_gas_non_negative"),
        sa.CheckConstraint(
            "load_factor IS NULL OR (load_factor > 0 AND load_factor <= 1)",
            name="ck_summary_load_factor",
        ),
        sa.CheckConstraint("date_trunc('day', day) = day", name="ck_summary_day_aligned"),
        schema=GOLD,
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_plant_daily_summary_scope
            ON {GOLD}.plant_daily_summary (plant_id, day)
        """
    )

    # ------------------------------------------------------------------
    # Foreign keys into reference are added after the tables exist so that
    # revision 0005 can convert these into hypertables without fighting a
    # dependency ordering problem.
    # ------------------------------------------------------------------
    for table, column in (
        ("kpi_hourly", "plant_id"),
        ("kpi_daily", "plant_id"),
        ("plant_daily_summary", "plant_id"),
    ):
        op.create_foreign_key(
            f"fk_{table}_plant",
            table,
            "plants",
            [column],
            ["plant_id"],
            source_schema=GOLD,
            referent_schema=REFERENCE,
            ondelete="CASCADE",
        )


def downgrade() -> None:
    """Drop the Gold schema."""
    op.execute(f"DROP SCHEMA IF EXISTS {GOLD} CASCADE")
