"""Give each issue its reasons, and let a draft correct an earlier one.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-18

Increment 6 of docs/frontend-plan.md. Everything here is in schema
``staging``; Bronze, Silver and Gold are untouched.

Reasons
    Every issue carries its likely reasons, most specific first, as a JSON
    array of ``{"id", "params"}``; ``reason_id`` repeats the first, which the
    issues list filters on. ``draft_reason_counts`` counts every issue by its
    first reason, listed or not, as ``draft_rule_counts`` counts them by rule.

Corrections
    Data is corrected in Excel and uploaded again (ADR 0009). The new upload
    is a draft of its own whose ``previous_draft_id`` names the draft it
    corrects. There is no foreign key: the earlier draft may expire and be
    purged first, and the correction must still know that there was one.
    ``match_key`` is the identity of an issue's row (its natural key,
    loosened, or its row number), which matches the issues of the two files.
    ``spec_digest`` records the spec a draft was validated against, so the
    comparison can tell whether the rules changed in between.

Entry modes
    Grid and form entry were dropped for every dataset (ADR 0009): a draft is
    always an upload. Every existing row is one, so the narrower check holds.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGING = "staging"
SHA256_HEX = "^[0-9a-f]{64}$"
# A reason's ID: dotted lowercase words, as ``code.lost_leading_zeros``.
REASON_ID = "^[a-z_]+(\\.[a-z_]+)+$"


def upgrade() -> None:
    """Add reasons, the row identity, the correction link and the spec digest."""
    # ------------------------------------------------------------------
    # staging.drafts
    # ------------------------------------------------------------------
    op.add_column(
        "drafts",
        sa.Column("previous_draft_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=STAGING,
    )
    op.add_column("drafts", sa.Column("spec_digest", sa.CHAR(64), nullable=True), schema=STAGING)
    op.create_check_constraint(
        "ck_drafts_previous_not_itself",
        "drafts",
        "previous_draft_id IS NULL OR previous_draft_id <> draft_id",
        schema=STAGING,
    )
    op.create_check_constraint(
        "ck_drafts_spec_digest_format",
        "drafts",
        f"spec_digest IS NULL OR spec_digest ~ '{SHA256_HEX}'",
        schema=STAGING,
    )
    op.create_index("ix_drafts_previous", "drafts", ["previous_draft_id"], schema=STAGING)
    op.drop_constraint("ck_drafts_input_mode", "drafts", schema=STAGING, type_="check")
    op.create_check_constraint(
        "ck_drafts_input_mode", "drafts", "input_mode = 'upload'", schema=STAGING
    )

    # ------------------------------------------------------------------
    # staging.draft_issues
    # ------------------------------------------------------------------
    op.add_column(
        "draft_issues",
        sa.Column(
            "reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema=STAGING,
    )
    op.add_column(
        "draft_issues", sa.Column("reason_id", sa.String(60), nullable=True), schema=STAGING
    )
    op.add_column(
        "draft_issues", sa.Column("match_key", sa.String(64), nullable=True), schema=STAGING
    )
    op.create_check_constraint(
        "ck_draft_issues_reasons_array",
        "draft_issues",
        "jsonb_typeof(reasons) = 'array'",
        schema=STAGING,
    )
    op.create_check_constraint(
        "ck_draft_issues_reason_id",
        "draft_issues",
        f"reason_id IS NULL OR reason_id ~ '{REASON_ID}'",
        schema=STAGING,
    )
    op.create_index(
        "ix_draft_issues_match",
        "draft_issues",
        ["draft_id", "rule_id", "column_key", "match_key"],
        schema=STAGING,
    )
    op.create_index(
        "ix_draft_issues_reason", "draft_issues", ["draft_id", "reason_id"], schema=STAGING
    )

    # ------------------------------------------------------------------
    # staging.draft_reason_counts - every issue, counted by its first reason
    # ------------------------------------------------------------------
    op.create_table(
        "draft_reason_counts",
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", sa.String(40), nullable=False),
        # Empty for the file as a whole, as in draft_rule_counts.
        sa.Column("column_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("reason_id", sa.String(60), nullable=False),
        sa.Column("rows_affected", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "draft_id", "rule_id", "column_key", "reason_id", name="pk_draft_reason_counts"
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            [f"{STAGING}.drafts.draft_id"],
            name="fk_draft_reason_counts_draft",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("rows_affected >= 1", name="ck_draft_reason_counts_rows"),
        sa.CheckConstraint(f"reason_id ~ '{REASON_ID}'", name="ck_draft_reason_counts_reason_id"),
        schema=STAGING,
    )


def downgrade() -> None:
    """Remove what upgrade added, and allow the three entry modes again."""
    op.drop_table("draft_reason_counts", schema=STAGING)
    op.drop_index("ix_draft_issues_reason", table_name="draft_issues", schema=STAGING)
    op.drop_index("ix_draft_issues_match", table_name="draft_issues", schema=STAGING)
    op.drop_column("draft_issues", "match_key", schema=STAGING)
    op.drop_column("draft_issues", "reason_id", schema=STAGING)
    op.drop_column("draft_issues", "reasons", schema=STAGING)
    op.drop_constraint("ck_drafts_input_mode", "drafts", schema=STAGING, type_="check")
    op.create_check_constraint(
        "ck_drafts_input_mode",
        "drafts",
        "input_mode IN ('upload', 'grid', 'form')",
        schema=STAGING,
    )
    op.drop_index("ix_drafts_previous", table_name="drafts", schema=STAGING)
    op.drop_column("drafts", "spec_digest", schema=STAGING)
    op.drop_column("drafts", "previous_draft_id", schema=STAGING)
