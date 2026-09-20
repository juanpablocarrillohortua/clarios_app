"""Application service for KPI computation and retrieval.

Layer: services

Responsibility:
    Coordinates the domain formulas, the reading repository and the KPI
    repository to answer "what is this plant's efficiency" and to populate Gold.
    It owns the transaction boundary for a KPI recomputation and the rule that a
    period is replaced wholesale rather than patched. It must never contain the
    formulas themselves - those live in
    :mod:`energy.domain.kpi_formulas` - and must never touch a concrete adapter,
    only the ports it is given.

Depends on:
    ``energy.domain``, ``energy.ports.kpi_repository``,
    ``energy.ports.reading_repository``, ``energy.core``.

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
    from energy.ports.kpi_repository import KPIRepository, KPIValue
    from energy.ports.reading_repository import ReadingRepository


class KPIService:
    """Computes and serves Gold-layer KPIs.

    Args:
        readings: port for reading validated Silver data.
        kpis: port for persisting and querying Gold values.
        kpi_definitions: parsed ``config/kpis.yaml``, mapping each KPI key to its
            formula, inputs, unit, and the granularities it is computed at.
    """

    def __init__(
        self,
        readings: ReadingRepository,
        kpis: KPIRepository,
        kpi_definitions: Mapping[str, object],
    ) -> None:
        raise NotImplementedError("Phase 4: KPI service construction")

    def compute(
        self,
        plant_id: UUID,
        window: TimeWindow,
        granularity: str,
        run_id: UUID,
        kpi_keys: Sequence[str] | None = None,
    ) -> Sequence[KPIValue]:
        """Compute every configured KPI for a plant and period.

        Reads Silver, applies the domain formulas, and returns the values without
        persisting them, so the caller can validate them against the Gold
        contract before committing.

        Args:
            plant_id: the plant to compute for.
            window: the half-open UTC period.
            granularity: the aggregation level to produce.
            run_id: the pipeline run to stamp on the results.
            kpi_keys: restrict to these KPIs; ``None`` computes all configured.

        Returns:
            One :class:`~energy.ports.kpi_repository.KPIValue` per KPI and
            period, each carrying its coverage fraction.

        Raises:
            UnknownKPIError: if a requested key is not configured.
            InvalidTimeWindowError: if the window is not aligned to the requested
                granularity.
        """
        raise NotImplementedError("Phase 4: KPI computation orchestration")

    def recompute_and_store(
        self,
        plant_id: UUID,
        window: TimeWindow,
        granularity: str,
        run_id: UUID,
    ) -> int:
        """Compute a period's KPIs and replace the stored values atomically.

        Args:
            plant_id: the plant to recompute.
            window: the half-open UTC period.
            granularity: the aggregation level.
            run_id: the pipeline run to attribute the write to.

        Returns:
            The number of KPI rows written.

        Raises:
            SchemaValidationError: if the computed values fail the Gold contract;
                nothing is written.
            RepositoryError: if the replacement transaction fails.
        """
        raise NotImplementedError("Phase 4: recompute and replace")

    def get_series(
        self,
        plant_code: str,
        kpi_key: str,
        window: TimeWindow,
        granularity: str,
    ) -> pl.DataFrame:
        """Read one KPI as a time series.

        Args:
            plant_code: the plant to read.
            kpi_key: which KPI.
            window: the half-open UTC period.
            granularity: the aggregation level.

        Returns:
            One row per period, with the value and its coverage.

        Raises:
            EntityNotFoundError: if the plant code is unknown.
            UnknownKPIError: if the KPI key is not configured.
        """
        raise NotImplementedError("Phase 4: KPI series retrieval")

    def get_summary(self, plant_codes: Sequence[str]) -> pl.DataFrame:
        """Read the latest value of every KPI for the given plants.

        Args:
            plant_codes: plants to include; empty means all.

        Returns:
            One row per plant and KPI with its latest value and the preceding
            period's value for a delta indicator.

        Raises:
            EntityNotFoundError: if a plant code is unknown.
        """
        raise NotImplementedError("Phase 4: cross-plant summary")


__all__ = ["KPIService"]
