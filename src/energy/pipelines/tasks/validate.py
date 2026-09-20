"""Task validating Bronze rows against the Silver contract.

Layer: pipelines

Responsibility:
    The quality gate between Bronze and Silver. Parses the raw JSONB payloads
    into typed columns, applies the Pandera Silver contract, and splits the
    result into rows that pass and rows that do not. It never raises on bad data
    and never repairs it: a value that fails is quarantined with the rule it
    broke, so the count entering always equals the count passing plus the count
    quarantined. That conservation property is what makes silent data loss
    impossible to hide.

Depends on:
    ``energy.schemas.silver``, ``energy.domain``, ``energy.core``, ``pandera``,
    ``polars``, ``prefect``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    import polars as pl


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    """Result of validating one batch.

    Attributes:
        valid: rows satisfying the Silver contract, ready to write.
        rejected: rows that failed, shaped for ``silver.quarantine``.
        rows_in: how many rows were submitted.
        rejection_rate: rejected divided by submitted, in ``[0, 1]``.
        rules_triggered: how many rows each failing rule accounted for, which is
            what tells a steward whether one column is broken or the whole file is.
    """

    valid: pl.DataFrame
    rejected: pl.DataFrame
    rows_in: int
    rejection_rate: float
    rules_triggered: dict[str, int]


def parse_bronze_payloads(frame: pl.DataFrame, source_key: str) -> pl.DataFrame:
    """Turn raw Bronze JSONB payloads into typed columns.

    Args:
        frame: Bronze rows with their ``payload`` struct column.
        source_key: the configured source, which determines the field mapping and
            the timezone the source's timestamps are expressed in.

    Returns:
        A frame with typed, canonically named columns and timestamps converted to
        timezone-aware UTC. Values that cannot be parsed become null and are
        caught by the contract rather than raising here.

    Raises:
        UnknownSourceError: if ``source_key`` is not configured.
    """
    raise NotImplementedError("Phase 3: Bronze payload parsing")


def validate_to_silver(
    frame: pl.DataFrame,
    run_id: UUID,
    max_rejection_rate: float = 0.05,
) -> ValidationOutcome:
    """Validate parsed rows and split them into accepted and quarantined.

    Args:
        frame: parsed, canonically named readings.
        run_id: the run to stamp on quarantined rows.
        max_rejection_rate: the share of rejected rows above which the batch is
            considered untrustworthy as a whole.

    Returns:
        A :class:`ValidationOutcome` conserving the row count.

    Raises:
        QuarantinedRowsError: if the rejection rate exceeds
            ``max_rejection_rate``. Nothing is written: when most of a file is
            wrong, loading the minority that happens to parse is worse than
            loading nothing.
    """
    raise NotImplementedError("Phase 3: Silver validation task")


__all__ = ["ValidationOutcome", "parse_bronze_payloads", "validate_to_silver"]
