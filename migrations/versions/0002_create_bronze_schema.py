"""Create the reference and Bronze schemas.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-18

This revision provisions two schemas rather than one, and the reason matters:

``reference``
    The shared dimension tables (plants, meters, shifts, targets). Silver and
    Gold both carry foreign keys into them, so they must exist before those
    layers are created. Rather than renumbering the five specified medallion
    migrations to insert a sixth, the Bronze revision - the first one to create
    any table at all - provisions them.

``bronze``
    The immutable landing area. Rows are appended and never updated; the only
    thing that removes them is the retention policy in revision 0005. The raw
    record is stored whole in a JSONB ``payload`` column rather than split into
    typed columns, so that a source adding a field needs no migration and a
    value that fails to parse is still preserved for diagnosis.

Every Bronze table carries the provenance columns listed in
``energy.core.constants.PROVENANCE_COLUMNS``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REFERENCE = "reference"
BRONZE = "bronze"


def upgrade() -> None:
    """Create the reference and Bronze schemas and their tables."""
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {REFERENCE}")
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {BRONZE}")

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

    # ------------------------------------------------------------------
    # bronze.ingestion_runs - the ledger every Bronze row points at
    # ------------------------------------------------------------------
    op.create_table(
        "ingestion_runs",
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("flow_name", sa.String(100), nullable=False),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column("plant_code", sa.String(32), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_runs_state",
        ),
        sa.CheckConstraint("row_count >= 0", name="ck_runs_row_count"),
        schema=BRONZE,
    )
    op.create_index(
        "ix_ingestion_runs_plant_started",
        "ingestion_runs",
        ["plant_code", sa.text("started_at DESC")],
        schema=BRONZE,
    )
    op.create_index("ix_ingestion_runs_state", "ingestion_runs", ["state"], schema=BRONZE)

    # ------------------------------------------------------------------
    # bronze.raw_files - catalogue of archived source payloads
    # ------------------------------------------------------------------
    op.create_table(
        "raw_files",
        sa.Column(
            "raw_file_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("content_hash", sa.CHAR(64), nullable=False, unique=True),
        sa.Column("bucket", sa.String(63), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(150), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("plant_code", sa.String(32), nullable=False),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("uploaded_by", sa.String(200), nullable=False, server_default="system"),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_raw_files_size"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_raw_files_hash_format"),
        schema=BRONZE,
    )
    op.create_index(
        "ix_raw_files_plant_uploaded",
        "raw_files",
        ["plant_code", sa.text("uploaded_at DESC")],
        schema=BRONZE,
    )

    # ------------------------------------------------------------------
    # bronze.raw_readings - the landing table (hypertable in revision 0005)
    # ------------------------------------------------------------------
    # The primary key is composite and includes `ingested_at` because
    # TimescaleDB requires the partitioning column in every unique constraint.
    op.create_table(
        "raw_readings",
        sa.Column(
            "raw_reading_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.CHAR(64), nullable=False),
        sa.Column("plant_code", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("raw_reading_id", "ingested_at", name="pk_raw_readings"),
        sa.CheckConstraint("source_row >= 0", name="ck_raw_readings_source_row"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_raw_readings_hash_format"),
        schema=BRONZE,
    )
    # Deduplication lookup: "have we already ingested this payload?"
    op.create_index("ix_raw_readings_content_hash", "raw_readings", ["content_hash"], schema=BRONZE)
    # Audit lookup: "show me every row this run wrote".
    op.create_index("ix_raw_readings_run", "raw_readings", ["ingestion_run_id"], schema=BRONZE)
    op.create_index(
        "ix_raw_readings_plant_source",
        "raw_readings",
        ["plant_code", "source_system", sa.text("ingested_at DESC")],
        schema=BRONZE,
    )


def downgrade() -> None:
    """Drop the Bronze and reference schemas."""
    op.execute(f"DROP SCHEMA IF EXISTS {BRONZE} CASCADE")
    op.execute(f"DROP SCHEMA IF EXISTS {REFERENCE} CASCADE")
