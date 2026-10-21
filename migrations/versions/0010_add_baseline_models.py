"""Keep the energy baseline models the web app shows (ADR 0012).

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-20

One table in schema ``gold``: ``baseline_models``. Each row is one trained
model, as ``energy models train`` fitted it: what it predicts and from what
(``spec``), its coefficients and their p-values, its training and validation
metrics, the history it was judged on, and the range of every variable it saw.
The API reads the current row of each model and evaluates its equation; the
web app draws it (``docs/adr/0012-the-app-shows-the-energy-baseline-models.md``).

Corrections supersede, never overwrite
    Training again does not update a row: it stamps ``superseded_at`` on the
    current row of each model and inserts a new one, in one transaction. The
    partial unique index lets each model have one current row only.

Not for Power BI
    ``reporting_reader`` is granted nothing here. The figures stay in Gold until
    a reporting view is decided for them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GOLD = "gold"

#: The models the app shows. Ensamble has none: its R² is too low (ADR 0012).
MODEL_KEYS: tuple[str, ...] = ("orec_mensual", "fa_mensual", "fa_diario")


def _sql_list(values: Sequence[str]) -> str:
    """Render values as the body of a SQL ``IN (...)`` list."""
    return ", ".join(f"'{value}'" for value in values)


def _jsonb(name: str) -> sa.Column:
    """A required JSONB column."""
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), nullable=False)


def upgrade() -> None:
    """Create ``gold.baseline_models`` and its one-current-row index."""
    op.create_table(
        "baseline_models",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("model_key", sa.Text(), nullable=False),
        sa.Column(
            "trained_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        _jsonb("spec"),
        _jsonb("coefficients"),
        _jsonb("metrics"),
        _jsonb("history"),
        _jsonb("feature_ranges"),
        sa.Column("training_start", sa.Date(), nullable=False),
        sa.Column("training_end", sa.Date(), nullable=False),
        sa.Column("n_training", sa.Integer(), nullable=False),
        sa.Column("coverage", sa.Numeric(5, 4), nullable=False),
        sa.CheckConstraint(
            f"model_key IN ({_sql_list(MODEL_KEYS)})", name="ck_baseline_models_key"
        ),
        sa.CheckConstraint(
            "training_start <= training_end", name="ck_baseline_models_window_ordered"
        ),
        sa.CheckConstraint("n_training > 0", name="ck_baseline_models_trained_on_rows"),
        sa.CheckConstraint(
            "coverage >= 0 AND coverage <= 1", name="ck_baseline_models_coverage_share"
        ),
        sa.CheckConstraint(
            "superseded_at IS NULL OR superseded_at >= trained_at",
            name="ck_baseline_models_superseded_after_trained",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(history) = 'array'", name="ck_baseline_models_history_array"
        ),
        schema=GOLD,
    )
    op.create_index(
        "ux_baseline_models_current",
        "baseline_models",
        ["model_key"],
        unique=True,
        schema=GOLD,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )


def downgrade() -> None:
    """Drop the table, and every model trained into it."""
    op.drop_index("ux_baseline_models_current", table_name="baseline_models", schema=GOLD)
    op.drop_table("baseline_models", schema=GOLD)
