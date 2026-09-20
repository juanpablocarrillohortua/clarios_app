"""SQLModel table definitions, one module per database schema.

Layer: db

These classes are the ORM mapping of the tables created by the Alembic
migrations under ``migrations/versions``. Migrations remain the source of
truth for DDL - in particular for the TimescaleDB hypertable, compression
and retention configuration, which SQLModel cannot express.
"""

from __future__ import annotations

__all__: list[str] = []
