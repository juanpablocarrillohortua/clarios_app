"""Literal values the entire platform agrees on.

Layer: core

Anything that appears in more than one layer as a magic string or number belongs
here: schema names, the canonical grid definition, provenance column names, and
the quality flags Silver rows carry. Nothing in this module performs I/O or
imports from any other ``energy`` package.
"""

from __future__ import annotations

from datetime import UTC, timedelta
from typing import Final

# -----------------------------------------------------------------------------
# Database schemas (one per medallion layer, plus shared dimensions)
# -----------------------------------------------------------------------------
REFERENCE_SCHEMA: Final = "reference"
BRONZE_SCHEMA: Final = "bronze"
SILVER_SCHEMA: Final = "silver"
GOLD_SCHEMA: Final = "gold"

MEDALLION_SCHEMAS: Final[tuple[str, ...]] = (BRONZE_SCHEMA, SILVER_SCHEMA, GOLD_SCHEMA)
ALL_SCHEMAS: Final[tuple[str, ...]] = (REFERENCE_SCHEMA, *MEDALLION_SCHEMAS)

# -----------------------------------------------------------------------------
# Canonical time grid
# -----------------------------------------------------------------------------
# Every Silver and Gold timestamp is an *interval start* on this grid, in UTC.
# Local plant time is a presentation concern and is never stored.
CANONICAL_TIMEZONE: Final = UTC
CANONICAL_GRID_FREQUENCY: Final = "1h"
CANONICAL_GRID_INTERVAL: Final = timedelta(hours=1)
SECONDS_PER_HOUR: Final = 3600

# -----------------------------------------------------------------------------
# Provenance columns carried by every Bronze row
# -----------------------------------------------------------------------------
# Bronze is immutable: rows are appended, never updated. These columns are what
# make a Bronze row auditable back to the byte range it came from.
PROVENANCE_COLUMNS: Final[tuple[str, ...]] = (
    "source_system",
    "source_uri",
    "source_row",
    "ingestion_run_id",
    "ingested_at",
    "content_hash",
)

# SHA-256 hex digest of the raw source payload; the deduplication key in Bronze.
CONTENT_HASH_ALGORITHM: Final = "sha256"
CONTENT_HASH_LENGTH: Final = 64

# -----------------------------------------------------------------------------
# Data quality
# -----------------------------------------------------------------------------
QUALITY_FLAG_OK: Final = "ok"
QUALITY_FLAG_INTERPOLATED: Final = "interpolated"
QUALITY_FLAG_ESTIMATED: Final = "estimated"
QUALITY_FLAG_SUSPECT: Final = "suspect"
QUALITY_FLAG_MISSING: Final = "missing"

QUALITY_FLAGS: Final[tuple[str, ...]] = (
    QUALITY_FLAG_OK,
    QUALITY_FLAG_INTERPOLATED,
    QUALITY_FLAG_ESTIMATED,
    QUALITY_FLAG_SUSPECT,
    QUALITY_FLAG_MISSING,
)

# -----------------------------------------------------------------------------
# Units of measure (canonical storage units)
# -----------------------------------------------------------------------------
# All electrical energy is stored in kWh, all gas volume in normal cubic metres,
# and all gas energy in kWh after applying the calorific value. Adapters convert
# on the way in; nothing downstream performs unit conversion.
UNIT_ELECTRICITY: Final = "kWh"
UNIT_GAS_VOLUME: Final = "Nm3"
UNIT_GAS_ENERGY: Final = "kWh"
UNIT_POWER: Final = "kW"
UNIT_PRODUCTION: Final = "equivalent_unit"

# -----------------------------------------------------------------------------
# Medallion layer names (used by logging, metrics, and the architecture test)
# -----------------------------------------------------------------------------
LAYER_BRONZE: Final = "bronze"
LAYER_SILVER: Final = "silver"
LAYER_GOLD: Final = "gold"

# -----------------------------------------------------------------------------
# Observability
# -----------------------------------------------------------------------------
REQUEST_ID_HEADER: Final = "X-Request-ID"
REQUEST_ID_LOG_KEY: Final = "request_id"

__all__ = [
    "ALL_SCHEMAS",
    "BRONZE_SCHEMA",
    "CANONICAL_GRID_FREQUENCY",
    "CANONICAL_GRID_INTERVAL",
    "CANONICAL_TIMEZONE",
    "CONTENT_HASH_ALGORITHM",
    "CONTENT_HASH_LENGTH",
    "GOLD_SCHEMA",
    "LAYER_BRONZE",
    "LAYER_GOLD",
    "LAYER_SILVER",
    "MEDALLION_SCHEMAS",
    "PROVENANCE_COLUMNS",
    "QUALITY_FLAGS",
    "QUALITY_FLAG_ESTIMATED",
    "QUALITY_FLAG_INTERPOLATED",
    "QUALITY_FLAG_MISSING",
    "QUALITY_FLAG_OK",
    "QUALITY_FLAG_SUSPECT",
    "REFERENCE_SCHEMA",
    "REQUEST_ID_HEADER",
    "REQUEST_ID_LOG_KEY",
    "SECONDS_PER_HOUR",
    "SILVER_SCHEMA",
    "UNIT_ELECTRICITY",
    "UNIT_GAS_ENERGY",
    "UNIT_GAS_VOLUME",
    "UNIT_POWER",
    "UNIT_PRODUCTION",
]
