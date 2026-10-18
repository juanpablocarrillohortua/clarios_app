"""Create the staging schema: drafts of data people load through the web app.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-17

A file uploaded through the web app is not landed at once. It becomes a draft:
its original is archived in object storage as it arrives, and validation reads
it back, stages its rows and records every problem found. Only a later commit
(increment 7 of docs/frontend-plan.md) lands the rows in Bronze, through the
same port the command-line loader uses.

A working area, not a medallion layer
    Nothing here is evidence: Bronze is. A draft may be validated again, which
    replaces what the previous validation staged, and an abandoned draft is
    purged after its ``expires_at`` (30 days after it was last worked on,
    question 17 of the plan). No foreign key points into Bronze, and nothing in
    Bronze points here.

Four tables
    ``drafts`` holds one row per upload: what was uploaded, by whom, where its
    original is archived and what its last validation found. ``draft_rows``
    holds the rows exactly as Bronze would land them (the same ``source_row``,
    ``content_hash`` and ``payload``). ``draft_issues`` lists the first issues
    of each rule in each column; ``draft_rule_counts`` counts every one, so a
    file broken on every row is reported exactly without a million rows.

Readable by the application only
    The schema belongs to the role the migrations run as, like every other.
    The read-only reporting login gets nothing here: drafts are not figures.

The statuses are the ones this increment uses. The commit adds its own with a
later revision.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGING = "staging"

# Defined here rather than imported from the application: a migration records
# what was applied, so it must not change when the application does.
SOURCE_KEYS: tuple[str, ...] = (
    "qad_rct_wo",
    "qad_facturas",
    "qad_product_struc",
    "item_master",
    "manual_sheets",
)
DRAFT_STATUSES: tuple[str, ...] = ("receiving", "uploaded", "validated", "failed")
INPUT_MODES: tuple[str, ...] = ("upload", "grid", "form")
SEVERITIES: tuple[str, ...] = ("error", "warning", "info")

SHA256_HEX = "^[0-9a-f]{64}$"
SNAKE_CASE = "^[a-z][a-z0-9_]*$"


def _sql_list(values: Sequence[str]) -> str:
    """Render values as the body of a SQL ``IN (...)`` list."""
    return ", ".join(f"'{value}'" for value in values)


def _draft_key() -> sa.Column[object]:
    """Return the column that ties a staged row to its draft."""
    return sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False)


def _to_draft(table: str) -> sa.ForeignKeyConstraint:
    """Return the foreign key from a staged table to its draft.

    Removing a draft removes everything staged for it: the purge is one delete.
    """
    return sa.ForeignKeyConstraint(
        ["draft_id"],
        [f"{STAGING}.drafts.draft_id"],
        name=f"fk_{table}_draft",
        ondelete="CASCADE",
    )


def upgrade() -> None:
    """Create the staging schema and its four tables."""
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {STAGING}")
    op.execute(f"REVOKE ALL ON SCHEMA {STAGING} FROM PUBLIC")

    # ------------------------------------------------------------------
    # staging.drafts - one upload, while it is prepared
    # ------------------------------------------------------------------
    op.create_table(
        "drafts",
        sa.Column(
            "draft_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # The dataset spec's key (config/datasets/<key>.yaml). Specs are files,
        # so this is not a foreign key.
        sa.Column("dataset_key", sa.String(64), nullable=False),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column("plant_code", sa.String(32), nullable=False),
        sa.Column("input_mode", sa.String(10), nullable=False, server_default="upload"),
        sa.Column("status", sa.String(20), nullable=False, server_default="receiving"),
        # Data only: shown to the person, never used as a key or a path.
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(150), nullable=False),
        sa.Column("declared_bytes", sa.BigInteger(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("content_hash", sa.CHAR(64), nullable=True),
        sa.Column("bucket", sa.String(63), nullable=True),
        sa.Column("object_key", sa.Text(), nullable=True),
        # The identity provider's subject (object ID) of the person.
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_by_name", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '30 days'"),
        ),
        sa.Column("failure_code", sa.String(64), nullable=True),
        # What the last validation found.
        sa.Column("sheet_name", sa.String(64), nullable=True),
        sa.Column("header_row", sa.Integer(), nullable=True),
        sa.Column("rows_read", sa.Integer(), nullable=True),
        sa.Column("rows_with_errors", sa.Integer(), nullable=True),
        sa.Column("rows_with_warnings", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("draft_id", name="pk_drafts"),
        sa.CheckConstraint(f"dataset_key ~ '{SNAKE_CASE}'", name="ck_drafts_dataset_key"),
        sa.CheckConstraint(
            f"source_key IN ({_sql_list(SOURCE_KEYS)})", name="ck_drafts_source_key"
        ),
        sa.CheckConstraint(
            f"input_mode IN ({_sql_list(INPUT_MODES)})", name="ck_drafts_input_mode"
        ),
        sa.CheckConstraint(f"status IN ({_sql_list(DRAFT_STATUSES)})", name="ck_drafts_status"),
        sa.CheckConstraint(
            "char_length(filename) BETWEEN 1 AND 255", name="ck_drafts_filename_length"
        ),
        sa.CheckConstraint("declared_bytes > 0", name="ck_drafts_declared_bytes"),
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes > 0", name="ck_drafts_size"),
        sa.CheckConstraint(
            f"content_hash IS NULL OR content_hash ~ '{SHA256_HEX}'",
            name="ck_drafts_hash_format",
        ),
        # An archived draft knows exactly where its original is.
        sa.CheckConstraint(
            "status NOT IN ('uploaded', 'validated') OR ("
            "size_bytes IS NOT NULL AND content_hash IS NOT NULL"
            " AND bucket IS NOT NULL AND object_key IS NOT NULL)",
            name="ck_drafts_archived",
        ),
        sa.CheckConstraint(
            "status <> 'validated' OR (validated_at IS NOT NULL AND rows_read IS NOT NULL)",
            name="ck_drafts_validated",
        ),
        sa.CheckConstraint(
            "rows_read IS NULL OR (rows_read >= 0 AND rows_with_errors BETWEEN 0 AND rows_read"
            " AND rows_with_warnings BETWEEN 0 AND rows_read)",
            name="ck_drafts_row_counts",
        ),
        schema=STAGING,
    )
    op.create_index(
        "ix_drafts_owner_created",
        "drafts",
        ["created_by", sa.text("created_at DESC")],
        schema=STAGING,
    )
    op.create_index("ix_drafts_expires", "drafts", ["expires_at"], schema=STAGING)
    op.create_index("ix_drafts_content_hash", "drafts", ["content_hash"], schema=STAGING)

    # ------------------------------------------------------------------
    # staging.draft_rows - the rows as Bronze would land them
    # ------------------------------------------------------------------
    op.create_table(
        "draft_rows",
        _draft_key(),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.CHAR(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("draft_id", "source_row", name="pk_draft_rows"),
        _to_draft("draft_rows"),
        sa.CheckConstraint("source_row >= 1", name="ck_draft_rows_source_row"),
        sa.CheckConstraint(f"content_hash ~ '{SHA256_HEX}'", name="ck_draft_rows_hash_format"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_draft_rows_payload_object"),
        schema=STAGING,
    )

    # ------------------------------------------------------------------
    # staging.draft_issues - the first issues of each rule in each column
    # ------------------------------------------------------------------
    op.create_table(
        "draft_issues",
        sa.Column("issue_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        _draft_key(),
        sa.Column("rule_id", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        # Null for the file as a whole; the header row for a column's problem.
        sa.Column("source_row", sa.Integer(), nullable=True),
        # 0-based Excel column (0 is A); null when not in one column of the file.
        sa.Column("column_index", sa.Integer(), nullable=True),
        sa.Column("column_key", sa.Text(), nullable=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("issue_id", name="pk_draft_issues"),
        _to_draft("draft_issues"),
        sa.CheckConstraint(
            f"severity IN ({_sql_list(SEVERITIES)})", name="ck_draft_issues_severity"
        ),
        sa.CheckConstraint(
            "source_row IS NULL OR source_row >= 1", name="ck_draft_issues_source_row"
        ),
        sa.CheckConstraint(
            "column_index IS NULL OR column_index >= 0", name="ck_draft_issues_column_index"
        ),
        sa.CheckConstraint(
            "value IS NULL OR char_length(value) <= 200", name="ck_draft_issues_value_length"
        ),
        sa.CheckConstraint("jsonb_typeof(params) = 'object'", name="ck_draft_issues_params_object"),
        schema=STAGING,
    )
    op.create_index(
        "ix_draft_issues_position",
        "draft_issues",
        ["draft_id", "source_row", "column_index"],
        schema=STAGING,
    )
    op.create_index(
        "ix_draft_issues_rule",
        "draft_issues",
        ["draft_id", "severity", "rule_id"],
        schema=STAGING,
    )

    # ------------------------------------------------------------------
    # staging.draft_rule_counts - every issue, counted
    # ------------------------------------------------------------------
    op.create_table(
        "draft_rule_counts",
        _draft_key(),
        sa.Column("rule_id", sa.String(40), nullable=False),
        # Empty for the file as a whole, so the key can include it.
        sa.Column("column_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("rows_affected", sa.BigInteger(), nullable=False),
        sa.Column("issues_listed", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("draft_id", "rule_id", "column_key", name="pk_draft_rule_counts"),
        _to_draft("draft_rule_counts"),
        sa.CheckConstraint(
            f"severity IN ({_sql_list(SEVERITIES)})", name="ck_draft_rule_counts_severity"
        ),
        sa.CheckConstraint(
            "issues_listed BETWEEN 0 AND rows_affected AND rows_affected >= 1",
            name="ck_draft_rule_counts_counts",
        ),
        schema=STAGING,
    )


def downgrade() -> None:
    """Drop the staging schema and every draft in it."""
    op.execute(f"DROP SCHEMA IF EXISTS {STAGING} CASCADE")
