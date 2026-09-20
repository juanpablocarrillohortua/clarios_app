"""Enable the PostgreSQL extensions the platform depends on.

Revision ID: 0001
Revises:
Create Date: 2026-09-18

Two extensions are required:

``timescaledb``
    Turns the Bronze, Silver and Gold time-series tables into hypertables, which
    is what makes chunk exclusion, native compression and retention policies
    available. Without it the schema still works but every time-range query
    degenerates into a full scan.

``pgcrypto``
    Supplies ``gen_random_uuid()`` for server-side primary keys, so a bulk COPY
    into Bronze does not have to generate millions of UUIDs client-side first.

The extension must be created before any other object, which is why this is the
first revision.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REQUIRED_EXTENSIONS: tuple[str, ...] = ("timescaledb", "pgcrypto")


def upgrade() -> None:
    """Create the required extensions."""
    for extension in REQUIRED_EXTENSIONS:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{extension}" CASCADE')


def downgrade() -> None:
    """Drop the extensions.

    Deliberately does **not** cascade: dropping ``timescaledb`` with CASCADE
    would silently destroy every hypertable and all their data. If the later
    revisions have not been reverted first, this fails loudly, which is the
    correct outcome.
    """
    for extension in reversed(REQUIRED_EXTENSIONS):
        op.execute(f'DROP EXTENSION IF EXISTS "{extension}"')
