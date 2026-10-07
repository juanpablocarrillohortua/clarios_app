"""Create the Bronze schema: the immutable landing area for source exports.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-18

Bronze is where every export the equivalent-batteries (EQUS) calculation
consumes lands before anything interprets it. The sources are spreadsheets: QAD
(the plant's ERP) explorations saved as Excel workbooks, plus a handful of
catalogues that people maintain by hand. One spreadsheet row becomes one Bronze
row. This revision creates only that landing area; the shared dimensions, Silver
and Gold belong to the revisions that follow.

Four decisions are encoded in this DDL:

The payload is stored verbatim
    Each row is kept whole in a JSONB ``payload`` whose keys are the header
    texts of the sheet exactly as exported - Spanish, with accents - and whose
    values are the cell text. Nothing is parsed or typed here: a date stays the
    text the workbook showed and ``N/A`` stays ``N/A``. The exports are known to
    carry defects (a header row pasted into the middle of a sheet, alphanumeric
    item codes, query metadata that no longer matches the rows). Parsing them
    here would either reject the row or silently repair it, and both destroy the
    evidence Silver needs to explain a number. It also means a column added to
    an export needs no migration.

Rows are append-only
    A landing row is never corrected in place. A trigger rejects every UPDATE,
    so the row a published figure was computed from cannot change afterwards; a
    correction is a new row written by a new run. DELETE stays allowed, because
    that is how a retention policy removes old rows.

Duplicates are preserved
    ``content_hash`` is indexed but deliberately not unique. The exports contain
    genuinely repeated rows (the same product-structure link pasted twice), and
    loading the same workbook again is legitimate. Collapsing them changes the
    result of the calculation, so it is a Silver decision that must stay visible
    and reversible. Bronze keeps every copy and lets the hash find them.

The primary key is composite
    Each landing table is keyed on ``(<table>_id, ingested_at)``. The UUID alone
    identifies a row, but TimescaleDB requires the partitioning column in every
    unique constraint; carrying ``ingested_at`` in the key now lets a later
    revision turn these tables into hypertables without rebuilding it.

Two tables are ledgers rather than landing tables. ``ingestion_runs`` records
each load and what the export said about itself; ``raw_files`` catalogues the
archived workbook a row came from. Neither is append-only: the loader moves a
run from ``pending`` to ``completed``.

``raw_readings``, the landing table of the energy-metering pipeline, predates
the EQUS tables and is created exactly as before; revision 0005 turns it into a
hypertable.
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

BRONZE = "bronze"

# Every value `source_key` may take, on the run ledger and the file catalogue.
SOURCE_KEYS: tuple[str, ...] = (
    "qad_rct_wo",
    "qad_facturas",
    "qad_product_struc",
    "item_master",
    "manual_sheets",
)

RUN_STATES: tuple[str, ...] = ("pending", "running", "completed", "failed", "cancelled")

MANUAL_SHEETS = "raw_manual_sheets"

# The landing tables. Table creation, the shared indexes and the append-only
# trigger all loop over this tuple, so none of them can miss a table.
#
#   raw_rct_wo         production receipts (QAD transaction type RCT-WO)
#   raw_facturas       invoice lines
#   raw_product_struc  product-structure links of every level, from two workbooks
#   raw_item_master    the item master and its family table
#   raw_manual_sheets  hand-maintained catalogues, told apart by `dataset_key`
LANDING_TABLES: tuple[str, ...] = (
    "raw_rct_wo",
    "raw_facturas",
    "raw_product_struc",
    "raw_item_master",
    MANUAL_SHEETS,
)

SHA256_HEX = "^[0-9a-f]{64}$"
SNAKE_CASE = "^[a-z][a-z0-9_]*$"

# (table, index suffix, payload key). The key is a header text of the export, so
# it must match the workbook character for character, accents included.
PAYLOAD_KEY_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("raw_rct_wo", "numero_de_transaccion", "Número de transacción"),
    ("raw_rct_wo", "fecha_efectiva", "Fecha Efectiva"),
    ("raw_facturas", "num_fact", "Núm Fact"),
    ("raw_facturas", "fecha_efectiva", "Fecha Efectiva"),
    ("raw_product_struc", "articulo_matriz", "Artículo Matriz"),
)

# (table, leading column). Answers "which is the latest load of this sheet?".
LATEST_LOAD_INDEXES: tuple[tuple[str, str], ...] = (
    ("raw_product_struc", "sheet_name"),
    ("raw_item_master", "sheet_name"),
    (MANUAL_SHEETS, "dataset_key"),
)


def _sql_list(values: Sequence[str]) -> str:
    """Render values as the body of a SQL ``IN (...)`` list."""
    return ", ".join(f"'{value}'" for value in values)


def _uuid_key(name: str) -> sa.Column[object]:
    """Return a UUID key column generated by the server."""
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    )


def _provenance_columns() -> list[sa.Column[object]]:
    """Return the columns every landing table carries, as new objects.

    Defined here rather than imported from the application: a migration records
    what was applied, so it must not change when the application does.
    """
    return [
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Null when the rows did not come from an archived workbook.
        sa.Column("raw_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("sheet_name", sa.String(64), nullable=False),
        # The Excel row number, so a value can be pointed at a specific cell.
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.CHAR(64), nullable=False),
        sa.Column("plant_code", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    ]


def _create_landing_table(table: str, *extra: sa.schema.SchemaItem) -> None:
    """Create one landing table with its key, provenance columns and shared indexes.

    Args:
        table: the table name, one of ``LANDING_TABLES``.
        extra: columns and constraints only this table has.
    """
    key = f"{table}_id"
    op.create_table(
        table,
        _uuid_key(key),
        *extra,
        *_provenance_columns(),
        sa.PrimaryKeyConstraint(key, "ingested_at", name=f"pk_{table}"),
        # RESTRICT on both: a ledger row must not disappear while evidence still
        # points at it, and SET NULL would be an UPDATE, which the append-only
        # trigger rejects.
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            [f"{BRONZE}.ingestion_runs.run_id"],
            name=f"fk_{table}_ingestion_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["raw_file_id"],
            [f"{BRONZE}.raw_files.raw_file_id"],
            name=f"fk_{table}_raw_file",
            ondelete="RESTRICT",
        ),
        # At least 1, not 2: some hand-made sheets have no header row.
        sa.CheckConstraint("source_row >= 1", name=f"ck_{table}_source_row"),
        sa.CheckConstraint(f"content_hash ~ '{SHA256_HEX}'", name=f"ck_{table}_hash_format"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name=f"ck_{table}_payload_object"),
        schema=BRONZE,
    )
    # Duplicate lookup: "has this exact row landed before?". Not unique.
    op.create_index(f"ix_{table}_content_hash", table, ["content_hash"], schema=BRONZE)
    # Audit lookups: "every row this run wrote" and "every row of this workbook".
    op.create_index(f"ix_{table}_run", table, ["ingestion_run_id"], schema=BRONZE)
    op.create_index(f"ix_{table}_raw_file", table, ["raw_file_id"], schema=BRONZE)


def upgrade() -> None:
    """Create the Bronze schema, its tables and the append-only guard."""
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {BRONZE}")

    source_key_list = _sql_list(SOURCE_KEYS)

    # ------------------------------------------------------------------
    # bronze.ingestion_runs - the ledger every Bronze row points at
    # ------------------------------------------------------------------
    op.create_table(
        "ingestion_runs",
        _uuid_key("run_id"),
        sa.Column("flow_name", sa.String(100), nullable=False),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column("plant_code", sa.String(32), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        # The business period the extract covers (its Fecha Efectiva range), in
        # dates because that is what the export filters on. Nullable: the
        # export's own search conditions can be stale, so the loader may have to
        # derive the window from the rows, or leave it open.
        sa.Column("window_start", sa.Date(), nullable=True),
        sa.Column("window_end", sa.Date(), nullable=True),
        # What the workbook's Info sheet says about the query that produced it.
        sa.Column("qad_program", sa.String(64), nullable=True),
        sa.Column("qad_exploration", sa.String(200), nullable=True),
        sa.Column("qad_user", sa.String(64), nullable=True),
        sa.Column("queried_at", sa.DateTime(timezone=True), nullable=True),
        # The rows of the workbook's search-conditions sheet, as delivered.
        sa.Column(
            "search_conditions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
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
        sa.PrimaryKeyConstraint("run_id", name="pk_ingestion_runs"),
        sa.CheckConstraint(
            f"state IN ({_sql_list(RUN_STATES)})",
            name="ck_ingestion_runs_state",
        ),
        sa.CheckConstraint(
            f"source_key IN ({source_key_list})",
            name="ck_ingestion_runs_source_key",
        ),
        sa.CheckConstraint("row_count >= 0", name="ck_ingestion_runs_row_count"),
        sa.CheckConstraint(
            "window_start IS NULL OR window_end IS NULL OR window_start <= window_end",
            name="ck_ingestion_runs_window_ordered",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(search_conditions) = 'array'",
            name="ck_ingestion_runs_search_conditions_array",
        ),
        schema=BRONZE,
    )
    op.create_index(
        "ix_ingestion_runs_source_started",
        "ingestion_runs",
        ["source_key", sa.text("started_at DESC")],
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
    # bronze.raw_files - catalogue of archived workbooks
    # ------------------------------------------------------------------
    op.create_table(
        "raw_files",
        _uuid_key("raw_file_id"),
        sa.Column("content_hash", sa.CHAR(64), nullable=False),
        sa.Column("bucket", sa.String(63), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(150), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("plant_code", sa.String(32), nullable=False),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column(
            "sheet_names",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("uploaded_by", sa.String(200), nullable=False, server_default="system"),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("raw_file_id", name="pk_raw_files"),
        # Unique here, unlike on the landing tables: the same bytes are archived
        # once, however many times the workbook is loaded.
        sa.UniqueConstraint("content_hash", name="uq_raw_files_content_hash"),
        sa.CheckConstraint(f"content_hash ~ '{SHA256_HEX}'", name="ck_raw_files_hash_format"),
        sa.CheckConstraint("size_bytes > 0", name="ck_raw_files_size"),
        sa.CheckConstraint(f"source_key IN ({source_key_list})", name="ck_raw_files_source_key"),
        schema=BRONZE,
    )
    op.create_index(
        "ix_raw_files_source_uploaded",
        "raw_files",
        ["source_key", sa.text("uploaded_at DESC")],
        schema=BRONZE,
    )

    # ------------------------------------------------------------------
    # The landing tables
    # ------------------------------------------------------------------
    for table in LANDING_TABLES:
        extra: tuple[sa.schema.SchemaItem, ...] = ()
        if table == MANUAL_SHEETS:
            # Names the catalogue (clasif_poly, lineas_produccion, instrucc,
            # element_deco, comparativo_lineas ...), so a new one needs a new
            # value rather than a new table and a migration.
            extra = (
                sa.Column("dataset_key", sa.String(64), nullable=False),
                sa.CheckConstraint(
                    f"dataset_key ~ '{SNAKE_CASE}'",
                    name=f"ck_{table}_dataset_key",
                ),
            )
        _create_landing_table(table, *extra)

    # The source's own key, read out of the payload. Bronze does not type the
    # value: the index is on the text exactly as exported.
    for table, suffix, payload_key in PAYLOAD_KEY_INDEXES:
        op.create_index(
            f"ix_{table}_{suffix}",
            table,
            [sa.text(f"(payload ->> '{payload_key}')")],
            schema=BRONZE,
        )
    for table, column in LATEST_LOAD_INDEXES:
        op.create_index(
            f"ix_{table}_{column}_ingested",
            table,
            [column, sa.text("ingested_at DESC")],
            schema=BRONZE,
        )

    # ------------------------------------------------------------------
    # Append-only guard
    # ------------------------------------------------------------------
    # UPDATE only. DELETE must keep working, because a retention policy deletes.
    #
    # The table to name arrives as the trigger argument rather than from
    # TG_TABLE_NAME: once a landing table is a hypertable the trigger fires on a
    # chunk, and "_hyper_6_1_chunk is append-only" tells the reader nothing.
    op.execute(
        f"""
        CREATE FUNCTION {BRONZE}.forbid_update() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only: UPDATE is not allowed',
                COALESCE(TG_ARGV[0], format('%I.%I', TG_TABLE_SCHEMA, TG_TABLE_NAME))
                USING ERRCODE = 'restrict_violation',
                      HINT = 'Land the corrected row in a new ingestion run.';
        END;
        $$
        """
    )
    for table in LANDING_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_forbid_update
                BEFORE UPDATE ON {BRONZE}.{table}
                FOR EACH ROW EXECUTE FUNCTION {BRONZE}.forbid_update('{BRONZE}.{table}')
            """
        )

    # ------------------------------------------------------------------
    # bronze.raw_readings - energy landing table (hypertable in revision 0005)
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
    """Drop the Bronze schema and everything in it.

    CASCADE is safe here, where it is not for the ``timescaledb`` extension in
    revision 0001. There, CASCADE reaches objects other revisions own and would
    destroy every hypertable. Here it reaches only what this revision created:
    the tables, their indexes and triggers, and ``forbid_update()`` all live
    inside the schema, and no later revision declares a foreign key into Bronze,
    so no object of another revision is removed. The data goes with it, which is
    what reverting the landing area means.
    """
    op.execute(f"DROP SCHEMA IF EXISTS {BRONZE} CASCADE")
