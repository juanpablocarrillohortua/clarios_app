"""Task computing Gold KPIs from Silver readings.

Layer: pipelines

Responsibility:
    Applies the pure formulas in :mod:`energy.domain.kpi_formulas` across a
    dataframe and writes the results to Gold. Its two substantive
    responsibilities are coverage accounting and correct roll-up. Coverage: every
    emitted row records what fraction of its period was backed by reliable
    readings, so a KPI computed from a half-observed day is distinguishable from
    a complete one. Roll-up: coarser granularities are recomputed from Silver
    with production weighting rather than averaged from finer KPI values, because
    the unweighted mean of a ratio is not the ratio of the sums.

Depends on:
    ``energy.domain.kpi_formulas``, ``energy.domain.equivalent_units``,
    ``energy.schemas.gold``, ``energy.services.kpi_service``, ``energy.core``,
    ``polars``, ``prefect``.

Implemented in:
    Phase 4 - KPI computation and Gold layer
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    import polars as pl

    from energy.domain.value_objects import TimeWindow


def compute_kpis(
    readings: pl.DataFrame,
    production: pl.DataFrame,
    kpi_definitions: Mapping[str, object],
    window: TimeWindow,
    granularity: str,
    run_id: UUID,
    kpi_keys: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Compute every configured KPI over a period.

    Args:
        readings: Silver readings covering the window.
        production: ERP production quantities per product and period.
        kpi_definitions: parsed ``config/kpis.yaml``.
        window: the half-open UTC period.
        granularity: the aggregation level to produce.
        run_id: the run to stamp on the results.
        kpi_keys: restrict to these KPIs; ``None`` computes all configured.

    Returns:
        A frame satisfying the Gold contract, one row per KPI and period, each
        with its coverage fraction.

    Raises:
        UnknownKPIError: if a requested key is not configured.
        SchemaValidationError: if the output fails the Gold contract.
    """
    raise NotImplementedError("Phase 4: KPI computation task")


def compute_coverage(readings: pl.DataFrame, window: TimeWindow, granularity: str) -> pl.DataFrame:
    """Measure what fraction of each period was backed by reliable readings.

    Reliable means a quality flag in ``ok``, ``interpolated`` or ``estimated``;
    ``suspect`` and ``missing`` intervals count against coverage.

    Args:
        readings: Silver readings covering the window.
        window: the period being measured.
        granularity: the period length to measure over.

    Returns:
        One row per meter and period with its coverage fraction in ``[0, 1]``.
    """
    raise NotImplementedError("Phase 4: coverage computation")


def roll_up(
    fine: pl.DataFrame,
    weights: pl.DataFrame,
    from_granularity: str,
    to_granularity: str,
) -> pl.DataFrame:
    """Aggregate KPI values to a coarser granularity with correct weighting.

    Intensive KPIs (ratios such as SEC) are weighted by their denominator;
    extensive KPIs (totals such as kWh) are summed. Which is which comes from the
    KPI definition, not from inspection, so a new KPI must declare its kind.

    Args:
        fine: KPI values at the finer granularity.
        weights: the weighting quantity per period, typically production.
        from_granularity: the granularity of ``fine``.
        to_granularity: the granularity to produce.

    Returns:
        KPI values at the coarser granularity, with coverage propagated as the
        weighted mean of the constituent coverages.

    Raises:
        ValueError: if ``to_granularity`` is not coarser than ``from_granularity``.
        ConfigurationError: if a KPI does not declare whether it is intensive or
            extensive.
    """
    raise NotImplementedError("Phase 4: granularity roll-up")


def build_daily_summary(
    kpis: pl.DataFrame,
    readings: pl.DataFrame,
    run_id: UUID,
) -> pl.DataFrame:
    """Build the denormalised per-plant daily summary rows.

    Args:
        kpis: computed daily KPI values.
        readings: the Silver readings behind them.
        run_id: the run to stamp on the rows.

    Returns:
        A frame satisfying the ``gold.plant_daily_summary`` contract, one row per
        plant per day.
    """
    raise NotImplementedError("Phase 4: daily summary assembly")


__all__ = ["build_daily_summary", "compute_coverage", "compute_kpis", "roll_up"]
