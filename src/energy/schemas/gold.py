"""Pandera contract for the Gold layer.

Layer: schemas

Responsibility:
    Defines the shape of the KPI tables that dashboards and the API read. The
    non-obvious guarantee enforced here is *coverage*: every Gold row carries the
    fraction of its period that was backed by reliable Silver readings, and that
    fraction is mandatory. A KPI row without coverage would let a consumer plot a
    value computed from two hours of data next to one computed from a full month
    as though they were equally trustworthy. This contract also enforces that
    period starts are grid-aligned for the declared granularity, so that
    aggregating daily values into monthly ones can never straddle a boundary.

Depends on:
    ``energy.core.constants``, ``pandera``, ``polars``.

Implemented in:
    Phase 4 - KPI computation and Gold layer
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandera.polars as pa
    import polars as pl

#: Granularities a Gold row may declare, coarsest last.
GRANULARITIES: tuple[str, ...] = ("hour", "day", "week", "month")

#: Minimum coverage for a KPI to be considered reportable without a caveat.
#: Rows below it are still stored - suppressing them would silently create gaps
#: in a chart - but consumers are expected to mark them as provisional.
REPORTABLE_COVERAGE_THRESHOLD = 0.8


def gold_kpi_schema() -> pa.DataFrameSchema:
    """Return the contract shared by ``gold.kpi_hourly`` and ``gold.kpi_daily``.

    Validated columns:
        ``plant_id``: non-null UUID.
        ``kpi_key``: non-null string, must exist in ``config/kpis.yaml``.
        ``period_start``: non-null timezone-aware UTC timestamp, aligned to the
            declared ``granularity``.
        ``granularity``: non-null, one of :data:`GRANULARITIES`.
        ``value``: non-null float and finite. Infinity is rejected: it means a
            KPI was computed with a zero denominator and should have been
            skipped instead.
        ``unit``: non-null string, for example ``"kWh/unit"``.
        ``coverage``: non-null float within ``[0, 1]``.
        ``computed_at``: non-null timezone-aware UTC timestamp.
        ``run_id``: non-null UUID of the run that produced the row.

    Uniqueness:
        ``(plant_id, kpi_key, period_start, granularity)`` must be unique.

    Returns:
        A strict Pandera schema.
    """
    raise NotImplementedError("Phase 4: Gold KPI contract")


def gold_plant_daily_summary_schema() -> pa.DataFrameSchema:
    """Return the contract for ``gold.plant_daily_summary``.

    The denormalised table dashboards read for their landing page: one row per
    plant per day with every headline KPI as a column, so a summary view is one
    indexed lookup rather than a pivot over the long KPI table.

    Validated columns:
        ``plant_id``: non-null UUID.
        ``day``: non-null UTC date, aligned to midnight.
        ``energy_kwh``: non-null non-negative float.
        ``gas_kwh``: non-null non-negative float.
        ``production_equivalent_units``: non-null non-negative float.
        ``sec_kwh_per_unit``: nullable; null when production was zero, which is
            correct and distinct from zero.
        ``load_factor``: nullable float within ``(0, 1]``.
        ``coverage``: non-null float within ``[0, 1]``.

    Returns:
        A strict Pandera schema.
    """
    raise NotImplementedError("Phase 4: plant daily summary contract")


def validate_gold(frame: pl.DataFrame, table: str) -> pl.DataFrame:
    """Validate a frame against the contract of one Gold table.

    Args:
        frame: the computed KPI rows about to be written.
        table: which Gold table the frame targets.

    Returns:
        The validated frame, with dtypes coerced to the contract.

    Raises:
        SchemaValidationError: if validation fails. Unlike Silver there is no
            quarantine path here: a KPI that fails its own contract is a
            computation bug, and writing a partial Gold period would leave the
            dashboards inconsistent.
        ValueError: if ``table`` is not a known Gold table.
    """
    raise NotImplementedError("Phase 4: Gold validation entry point")


__all__ = [
    "GRANULARITIES",
    "REPORTABLE_COVERAGE_THRESHOLD",
    "gold_kpi_schema",
    "gold_plant_daily_summary_schema",
    "validate_gold",
]
