"""Pandera contract for the Bronze layer.

Layer: schemas

Responsibility:
    Defines what the platform guarantees about raw ingested data - which is
    deliberately very little. Bronze accepts whatever arrived, so this contract
    validates only the provenance envelope: every row must carry its source,
    its content hash, its run id, and an ingestion timestamp. The *payload*
    columns are intentionally unconstrained, because rejecting a malformed value
    at Bronze would destroy the evidence needed to diagnose it. Type coercion,
    range checks and business validation all happen at the Silver boundary.

Depends on:
    ``energy.core.constants``, ``pandera``, ``polars``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandera.polars as pa
    import polars as pl


def bronze_raw_readings_schema() -> pa.DataFrameSchema:
    """Return the contract for ``bronze.raw_readings``.

    Validated columns:
        ``source_system``: non-null string, must match a configured source key.
        ``source_uri``: non-null string locating the original payload.
        ``source_row``: non-null integer, the 1-based row within the payload, so
            a rejected value can be pointed at a specific spreadsheet cell.
        ``ingestion_run_id``: non-null UUID.
        ``ingested_at``: non-null timezone-aware UTC timestamp.
        ``content_hash``: non-null 64-character lowercase hex SHA-256.
        ``payload``: non-null struct holding the raw record exactly as read.

    Returns:
        A Pandera schema with ``strict=False``, because sources legitimately
        carry extra columns that Bronze preserves rather than drops.
    """
    raise NotImplementedError("Phase 3: Bronze provenance contract")


def bronze_ingestion_runs_schema() -> pa.DataFrameSchema:
    """Return the contract for ``bronze.ingestion_runs``.

    Validated columns:
        ``run_id``: non-null unique UUID.
        ``flow_name``: non-null string.
        ``source_system``: non-null string.
        ``plant_code``: non-null string.
        ``started_at``: non-null timezone-aware UTC timestamp.
        ``finished_at``: nullable; null means the run is still in progress.
        ``state``: one of pending, running, completed, failed, cancelled.
        ``row_count``: non-negative integer.

    Returns:
        A Pandera schema for the ingestion run ledger.
    """
    raise NotImplementedError("Phase 3: ingestion run contract")


def validate_bronze(frame: pl.DataFrame) -> pl.DataFrame:
    """Validate a frame against the Bronze contract.

    Args:
        frame: the frame about to be written to Bronze, with provenance columns
            already stamped.

    Returns:
        The same frame, unchanged, once validation passes. Bronze validation
        never coerces or repairs: altering a raw row would defeat the purpose of
        keeping it.

    Raises:
        SchemaValidationError: if any provenance column is missing or malformed.
            This is a bug in the ingestion code, not bad source data, so it fails
            the run rather than quarantining rows.
    """
    raise NotImplementedError("Phase 3: Bronze validation entry point")


__all__ = [
    "bronze_ingestion_runs_schema",
    "bronze_raw_readings_schema",
    "validate_bronze",
]
