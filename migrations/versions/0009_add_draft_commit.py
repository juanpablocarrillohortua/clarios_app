"""Let a validated draft be committed, and keep what the commit needs to describe it.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-18

Increment 7 of docs/frontend-plan.md. Everything here is in schema
``staging``; Bronze is written by the commit itself, in its own transaction,
and its tables do not change (frontend plan §6, "no new Bronze column").

Committed
    A draft that was committed is ``committed`` and names the run that landed
    it (``run_id``) and when (``committed_at``). There is no foreign key into
    Bronze: staging is a working area, and nothing here may stop Bronze from
    being what it is. Its staged rows are deleted by the commit, because Bronze
    holds them now; its issues and counts stay until the draft is purged.

What the export says about itself
    The run a commit opens carries what the command-line loader records for
    the same file: the workbook's sheets, the first and the last date of the
    period column, what its "Info" sheet says about the query that produced
    it, and its search conditions. They are read once, at validation, when
    the workbook is open, and kept here (``sheet_names``, ``window_start``,
    ``window_end``, ``export_info``, ``search_conditions``). A draft validated
    before this revision has none of them and is validated again before it
    is committed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGING = "staging"

STATUSES_BEFORE: tuple[str, ...] = ("receiving", "uploaded", "validated", "failed")
STATUSES: tuple[str, ...] = (*STATUSES_BEFORE, "committed")


def _sql_list(values: Sequence[str]) -> str:
    """Render values as the body of a SQL ``IN (...)`` list."""
    return ", ".join(f"'{value}'" for value in values)


def _replace_check(name: str, condition: str) -> None:
    """Replace one check constraint of ``staging.drafts``."""
    op.drop_constraint(name, "drafts", schema=STAGING, type_="check")
    op.create_check_constraint(name, "drafts", condition, schema=STAGING)


def upgrade() -> None:
    """Add the committed state, the run it names, and the export's own facts."""
    op.add_column(
        "drafts", sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True), schema=STAGING
    )
    op.add_column(
        "drafts",
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        schema=STAGING,
    )
    op.add_column(
        "drafts",
        sa.Column("sheet_names", postgresql.ARRAY(sa.Text()), nullable=True),
        schema=STAGING,
    )
    op.add_column("drafts", sa.Column("window_start", sa.Date(), nullable=True), schema=STAGING)
    op.add_column("drafts", sa.Column("window_end", sa.Date(), nullable=True), schema=STAGING)
    op.add_column(
        "drafts",
        sa.Column("export_info", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema=STAGING,
    )
    op.add_column(
        "drafts",
        sa.Column("search_conditions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema=STAGING,
    )

    _replace_check("ck_drafts_status", f"status IN ({_sql_list(STATUSES)})")
    # A committed draft was archived and validated, as a validated one was.
    _replace_check(
        "ck_drafts_archived",
        "status NOT IN ('uploaded', 'validated', 'committed') OR ("
        "size_bytes IS NOT NULL AND content_hash IS NOT NULL"
        " AND bucket IS NOT NULL AND object_key IS NOT NULL)",
    )
    _replace_check(
        "ck_drafts_validated",
        "status NOT IN ('validated', 'committed')"
        " OR (validated_at IS NOT NULL AND rows_read IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_drafts_committed",
        "drafts",
        "(status = 'committed') = (run_id IS NOT NULL AND committed_at IS NOT NULL)",
        schema=STAGING,
    )
    op.create_check_constraint(
        "ck_drafts_window_ordered",
        "drafts",
        "window_start IS NULL OR window_end IS NULL OR window_start <= window_end",
        schema=STAGING,
    )
    op.create_check_constraint(
        "ck_drafts_export_info_object",
        "drafts",
        "export_info IS NULL OR jsonb_typeof(export_info) = 'object'",
        schema=STAGING,
    )
    op.create_check_constraint(
        "ck_drafts_search_conditions_array",
        "drafts",
        "search_conditions IS NULL OR jsonb_typeof(search_conditions) = 'array'",
        schema=STAGING,
    )
    op.create_index("ix_drafts_run", "drafts", ["run_id"], schema=STAGING)


def downgrade() -> None:
    """Remove what upgrade added. Refused while a draft is committed.

    A committed draft cannot be told apart from a validated one without these
    columns, and turning it back into one would let it be committed twice.
    """
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM {STAGING}.drafts WHERE status = 'committed') THEN
                RAISE EXCEPTION 'committed drafts exist: purge them before downgrading';
            END IF;
        END
        $$
        """  # noqa: S608 - the schema's name is a constant
    )
    op.drop_index("ix_drafts_run", table_name="drafts", schema=STAGING)
    for name in (
        "ck_drafts_search_conditions_array",
        "ck_drafts_export_info_object",
        "ck_drafts_window_ordered",
        "ck_drafts_committed",
    ):
        op.drop_constraint(name, "drafts", schema=STAGING, type_="check")
    _replace_check(
        "ck_drafts_validated",
        "status <> 'validated' OR (validated_at IS NOT NULL AND rows_read IS NOT NULL)",
    )
    _replace_check(
        "ck_drafts_archived",
        "status NOT IN ('uploaded', 'validated') OR ("
        "size_bytes IS NOT NULL AND content_hash IS NOT NULL"
        " AND bucket IS NOT NULL AND object_key IS NOT NULL)",
    )
    _replace_check("ck_drafts_status", f"status IN ({_sql_list(STATUSES_BEFORE)})")
    for column in (
        "search_conditions",
        "export_info",
        "window_end",
        "window_start",
        "sheet_names",
        "committed_at",
        "run_id",
    ):
        op.drop_column("drafts", column, schema=STAGING)
