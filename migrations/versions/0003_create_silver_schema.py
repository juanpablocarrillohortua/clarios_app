"""Create the Silver schema: validated readings and the quarantine ledger.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-18

Silver holds readings that are validated, deduplicated and aligned to the
canonical hourly UTC grid. Three design decisions are encoded in this DDL:

Supersede, never delete
    A corrected reading does not overwrite its predecessor; it marks the old row
    ``superseded_at`` and inserts a new one. Uniqueness on ``(meter_id, ts_utc)``
    is therefore enforced by a *partial* unique index covering only live rows,
    which is what lets history and correction coexist in one table.

Quarantine is a peer, not a footnote
    ``silver.quarantine`` sits beside ``silver.readings`` and receives every row
    that fails validation. Rows in equals rows landed plus rows quarantined, and
    that conservation property is what makes silent data loss detectable.

Conflicts are audited
    ``silver.consolidation_audit`` records every interval where two sources
    disagreed, including the values that lost. Without it, a user whose
    spreadsheet disagrees with the dashboard has no way to discover that a
    higher-priority source overrode their number.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REFERENCE = "reference"
SILVER = "silver"

QUALITY_FLAGS = ("ok", "interpolated", "estimated", "suspect", "missing")


def upgrade() -> None:
    """Create the Silver schema and its tables."""
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SILVER}")

    quality_list = ", ".join(f"'{flag}'" for flag in QUALITY_FLAGS)

    # ------------------------------------------------------------------
    # silver.readings - hypertable in revision 0005
    # ------------------------------------------------------------------
    op.create_table(
        "readings",
        sa.Column(
            "reading_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("ts_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("value", sa.Double(), nullable=True),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("quality", sa.String(20), nullable=False, server_default="ok"),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column(
            "source_priority",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("100"),
        ),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("reading_id", "ts_utc", name="pk_silver_readings"),
        sa.CheckConstraint(f"quality IN ({quality_list})", name="ck_readings_quality"),
        # A `missing` row carries no value; any other flag must carry one. This is
        # what keeps "no data" from being stored as zero.
        sa.CheckConstraint(
            "(quality = 'missing' AND value IS NULL) "
            "OR (quality <> 'missing' AND value IS NOT NULL)",
            name="ck_readings_missing_has_no_value",
        ),
        sa.CheckConstraint(
            "value IS NULL OR (value = value AND value <> 'Infinity'::double precision "
            "AND value <> '-Infinity'::double precision)",
            name="ck_readings_value_finite",
        ),
        # The canonical grid is hourly: timestamps must land exactly on the hour.
        sa.CheckConstraint("date_trunc('hour', ts_utc) = ts_utc", name="ck_readings_grid_aligned"),
        schema=SILVER,
    )

    # Uniqueness applies only to live rows, so a superseded correction can sit
    # beside the value that replaced it. Includes ts_utc for TimescaleDB.
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_silver_readings_live
            ON {SILVER}.readings (meter_id, ts_utc)
            WHERE superseded_at IS NULL
        """
    )
    op.create_index(
        "ix_silver_readings_plant_ts",
        "readings",
        ["plant_id", sa.text("ts_utc DESC")],
        schema=SILVER,
    )
    op.create_index("ix_silver_readings_run", "readings", ["ingestion_run_id"], schema=SILVER)

    # ------------------------------------------------------------------
    # silver.quarantine
    # ------------------------------------------------------------------
    op.create_table(
        "quarantine",
        sa.Column(
            "quarantine_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bronze_row_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("plant_code", sa.String(32), nullable=False),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column("rule", sa.String(100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "quarantined_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=False, server_default=""),
        schema=SILVER,
    )
    op.create_index("ix_quarantine_run", "quarantine", ["run_id"], schema=SILVER)
    op.create_index("ix_quarantine_rule", "quarantine", ["rule"], schema=SILVER)
    # Partial index: the working queue a data steward actually looks at.
    op.execute(
        f"""
        CREATE INDEX ix_quarantine_unresolved
            ON {SILVER}.quarantine (plant_code, quarantined_at DESC)
            WHERE resolved_at IS NULL
        """
    )

    # ------------------------------------------------------------------
    # silver.consolidation_audit
    # ------------------------------------------------------------------
    op.create_table(
        "consolidation_audit",
        sa.Column(
            "audit_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("ts_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("winning_source", sa.String(64), nullable=False),
        sa.Column("losing_sources", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("winning_value", sa.Double(), nullable=True),
        sa.Column("losing_values", postgresql.ARRAY(sa.Double()), nullable=False),
        sa.Column("divergence_pct", sa.Double(), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["meter_id"], [f"{REFERENCE}.meters.meter_id"], ondelete="CASCADE"),
        schema=SILVER,
    )
    op.create_index(
        "ix_consolidation_audit_meter_ts",
        "consolidation_audit",
        ["meter_id", sa.text("ts_utc DESC")],
        schema=SILVER,
    )
    # Surfaces the intervals where sources disagreed materially.
    op.execute(
        f"""
        CREATE INDEX ix_consolidation_audit_divergent
            ON {SILVER}.consolidation_audit (divergence_pct DESC)
            WHERE divergence_pct > 2.0
        """
    )


def downgrade() -> None:
    """Drop the Silver schema."""
    op.execute(f"DROP SCHEMA IF EXISTS {SILVER} CASCADE")
