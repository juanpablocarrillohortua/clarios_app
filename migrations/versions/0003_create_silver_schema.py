"""Create the reference and Silver schemas: dimensions, readings, quarantine.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-18

This revision provisions two schemas rather than one, and the reason matters:

``reference``
    The shared dimension tables (plants, meters, shifts, targets). Silver and
    Gold both carry foreign keys into them, so they must exist before either
    layer does. Silver is the first to need them, so they are created here
    rather than in a revision of their own, which would have meant renumbering
    the medallion migrations. Bronze has no use for them: it stores what arrived
    and resolves nothing.

``silver``
    Readings that are validated, deduplicated and aligned to the canonical
    hourly UTC grid.

Three design decisions are encoded in the Silver DDL:

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
    """Create the reference and Silver schemas and their tables."""
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {REFERENCE}")
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SILVER}")

    # ------------------------------------------------------------------
    # reference.plants
    # ------------------------------------------------------------------
    op.create_table(
        "plants",
        sa.Column(
            "plant_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("floor_area_m2", sa.Numeric(14, 2), nullable=True),
        sa.Column("commissioned_on", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("floor_area_m2 IS NULL OR floor_area_m2 > 0", name="ck_plants_area"),
        schema=REFERENCE,
    )

    # ------------------------------------------------------------------
    # reference.meters
    # ------------------------------------------------------------------
    op.create_table(
        "meters",
        sa.Column(
            "meter_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("plant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag", sa.String(64), nullable=False),
        sa.Column("commodity", sa.String(20), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("multiplier", sa.Numeric(18, 6), nullable=False, server_default=sa.text("1.0")),
        sa.Column("is_virtual", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("parent_meter_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("installed_on", sa.Date(), nullable=True),
        sa.Column("removed_on", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["plant_id"], [f"{REFERENCE}.plants.plant_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_meter_id"], [f"{REFERENCE}.meters.meter_id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("plant_id", "tag", name="uq_meters_plant_tag"),
        sa.CheckConstraint(
            "commodity IN ('electricity', 'gas', 'steam', 'water', 'production')",
            name="ck_meters_commodity",
        ),
        sa.CheckConstraint("multiplier > 0", name="ck_meters_multiplier"),
        schema=REFERENCE,
    )

    # ------------------------------------------------------------------
    # reference.shifts
    # ------------------------------------------------------------------
    op.create_table(
        "shifts",
        sa.Column(
            "shift_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("plant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=False),
        sa.Column("ends_at", sa.Time(), nullable=False),
        sa.Column("weekdays", postgresql.ARRAY(sa.SmallInteger()), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["plant_id"], [f"{REFERENCE}.plants.plant_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("plant_id", "name", "valid_from", name="uq_shifts_plant_name_from"),
        schema=REFERENCE,
    )

    # ------------------------------------------------------------------
    # reference.targets - temporally versioned
    # ------------------------------------------------------------------
    op.create_table(
        "targets",
        sa.Column(
            "target_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("plant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kpi_key", sa.String(64), nullable=False),
        sa.Column("value", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("direction", sa.String(10), nullable=False, server_default="lower"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["plant_id"], [f"{REFERENCE}.plants.plant_id"], ondelete="CASCADE"),
        sa.CheckConstraint("direction IN ('lower', 'higher')", name="ck_targets_direction"),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_targets_validity"),
        sa.UniqueConstraint("plant_id", "kpi_key", "valid_from", name="uq_targets_scope_from"),
        schema=REFERENCE,
    )

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
    """Drop the Silver and reference schemas.

    Silver goes first because it holds a foreign key into ``reference``. Gold,
    the other layer that references it, is already gone by the time this runs.
    """
    op.execute(f"DROP SCHEMA IF EXISTS {SILVER} CASCADE")
    op.execute(f"DROP SCHEMA IF EXISTS {REFERENCE} CASCADE")
