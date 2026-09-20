"""Pandera contract for the Silver layer.

Layer: schemas

Responsibility:
    Defines what "validated" means in this platform. A frame satisfying this
    contract is deduplicated, typed, aligned to the canonical hourly UTC grid,
    and physically plausible. This is the strictest contract of the three, and
    it is the boundary where bad data stops: rows that fail are routed to
    ``silver.quarantine`` with their failure reason rather than dropped, so a
    data steward can see exactly what was rejected and why. Everything
    downstream - KPIs, dashboards, models - is written assuming these guarantees
    hold, and therefore performs no defensive checking of its own.

Depends on:
    ``energy.core.constants``, ``energy.domain.exceptions``, ``pandera``,
    ``polars``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandera.polars as pa
    import polars as pl

#: Upper bound on a single hourly reading, in kWh. A value above this is far
#: outside any plausible plant load and indicates a unit error or a register
#: rollover rather than real consumption.
MAX_HOURLY_KWH = 10_000_000.0


def silver_readings_schema() -> pa.DataFrameSchema:
    """Return the contract for ``silver.readings``.

    Validated columns:
        ``meter_id``: non-null UUID, must exist in ``reference.meters``.
        ``plant_id``: non-null UUID, must exist in ``reference.plants``.
        ``ts_utc``: non-null timezone-aware UTC timestamp, aligned to the
            canonical grid. Unaligned timestamps are a consolidation bug.
        ``value``: non-null float, finite, within ``[0, MAX_HOURLY_KWH]``.
            Negative consumption is quarantined rather than clipped, because it
            usually means a meter was replaced and the register reset.
        ``unit``: non-null, one of the canonical units.
        ``quality``: non-null, one of
            :data:`energy.core.constants.QUALITY_FLAGS`.
        ``source_system``: non-null string, retained so a Silver row can still be
            traced back to Bronze.
        ``superseded_at``: nullable timestamp; non-null marks a row replaced by a
            later correction.

    Uniqueness:
        ``(meter_id, ts_utc)`` must be unique among non-superseded rows. This is
        the guarantee that makes summing Silver safe.

    Returns:
        A Pandera schema with ``strict=True``: unexpected columns are an error,
        because Silver's shape is a contract other layers code against.
    """
    raise NotImplementedError("Phase 3: Silver readings contract")


def silver_quarantine_schema() -> pa.DataFrameSchema:
    """Return the contract for ``silver.quarantine``.

    Validated columns:
        ``quarantine_id``: non-null unique UUID.
        ``run_id``: non-null UUID of the run that rejected the row.
        ``bronze_row_id``: non-null reference back to the offending Bronze row.
        ``rule``: non-null string naming the validation rule that failed.
        ``reason``: non-null human-readable explanation, in English.
        ``raw_payload``: non-null struct with the original values.
        ``quarantined_at``: non-null timezone-aware UTC timestamp.
        ``resolved_at``: nullable; set when a steward has dealt with the row.

    Returns:
        A Pandera schema for the rejected-row ledger.
    """
    raise NotImplementedError("Phase 3: quarantine contract")


def validate_silver(frame: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split a frame into rows that satisfy the Silver contract and rows that do not.

    This is the central data-quality gate. It must never raise on bad *data* -
    only on a malformed call - because a single bad row in a million-row batch
    should quarantine one row, not fail a plant's whole nightly load.

    Args:
        frame: candidate readings, already consolidated onto the canonical grid.

    Returns:
        A two-tuple of ``(valid, rejected)``. ``valid`` satisfies
        :func:`silver_readings_schema` and is ready to write. ``rejected``
        satisfies :func:`silver_quarantine_schema`, with one row per failure
        carrying the rule that failed and the original values.

    Raises:
        SchemaValidationError: only if ``frame`` is missing columns required to
            even attempt validation, which indicates a pipeline bug.
    """
    raise NotImplementedError("Phase 3: Silver validation and quarantine split")


__all__ = [
    "MAX_HOURLY_KWH",
    "silver_quarantine_schema",
    "silver_readings_schema",
    "validate_silver",
]
