"""Port for persisting and querying the Gold KPI layer.

Layer: ports

Responsibility:
    Declares how computed KPIs are written to Gold and read back by the API and
    the dashboards. Writes are idempotent replacements scoped to a period, so
    recomputing a day after a late correction arrives yields exactly one set of
    values rather than duplicates. Reads are the only path the presentation
    layers use: a chart and an API response must resolve to the same stored row,
    never to a figure recomputed on the fly. This protocol knows nothing about
    SQL, continuous aggregates, or caching.

Depends on:
    ``energy.domain``, standard library typing only.

Implemented in:
    Phase 4 - KPI computation and Gold layer
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from decimal import Decimal
    from uuid import UUID

    import polars as pl

    from energy.domain.value_objects import TimeWindow


@dataclass(frozen=True, slots=True)
class KPIValue:
    """One computed KPI for one plant over one period.

    Attributes:
        plant_id: the plant the value belongs to.
        kpi_key: which KPI, matching a key in ``config/kpis.yaml``.
        period_start: start of the period, timezone-aware UTC and aligned to the
            canonical grid.
        granularity: ``"hour"``, ``"day"``, ``"week"``, or ``"month"``.
        value: the computed number.
        unit: the unit the value is expressed in, for example ``"kWh/unit"``.
        coverage: fraction of the period backed by reliable readings, in
            ``[0, 1]``. A KPI computed from half-missing data is reported with
            its coverage rather than suppressed, so the consumer can decide.
        computed_at: when this value was produced, timezone-aware UTC.
        run_id: the pipeline run that produced it.
    """

    plant_id: UUID
    kpi_key: str
    period_start: datetime
    granularity: str
    value: Decimal
    unit: str
    coverage: Decimal
    computed_at: datetime
    run_id: UUID


@runtime_checkable
class KPIRepository(Protocol):
    """Interface for writing and querying Gold-layer KPI values."""

    def replace_period(
        self,
        plant_id: UUID,
        window: TimeWindow,
        granularity: str,
        values: Sequence[KPIValue],
    ) -> int:
        """Atomically replace every KPI value for a plant, period, and granularity.

        Delete-then-insert inside one transaction rather than upsert-per-row: a
        recomputation that produces *fewer* values than before (because readings
        were quarantined) must not leave the surplus rows behind.

        Args:
            plant_id: the plant whose values are being replaced.
            window: the period to replace, half-open UTC.
            granularity: which granularity is being replaced.
            values: the complete new set of values for that scope.

        Returns:
            The number of rows written.

        Raises:
            RepositoryError: if the transaction fails; nothing is committed.
        """
        ...

    def query(
        self,
        plant_ids: Sequence[UUID],
        kpi_keys: Sequence[str],
        window: TimeWindow,
        granularity: str,
    ) -> pl.DataFrame:
        """Read KPI values for dashboards and API responses.

        Args:
            plant_ids: plants to include; empty means every plant the caller may
                see.
            kpi_keys: KPIs to include; empty means every configured KPI.
            window: the UTC period to read, half-open.
            granularity: the granularity to read.

        Returns:
            A long-format frame with one row per
            ``(plant_id, kpi_key, period_start)``. Periods with no stored value
            are absent rather than null-filled.

        Raises:
            UnknownKPIError: if a requested key is not defined in ``kpis.yaml``.
            RepositoryError: if the query fails.
        """
        ...

    def latest(self, plant_id: UUID, kpi_key: str, granularity: str) -> KPIValue | None:
        """Return the most recent stored value of one KPI.

        Args:
            plant_id: the plant to read.
            kpi_key: which KPI.
            granularity: which granularity.

        Returns:
            The newest :class:`KPIValue`, or ``None`` if the KPI has never been
            computed for this plant.
        """
        ...

    def coverage_report(self, plant_id: UUID, window: TimeWindow) -> pl.DataFrame:
        """Summarise data coverage behind the KPIs of a period.

        Args:
            plant_id: the plant to report on.
            window: the UTC period to summarise.

        Returns:
            One row per KPI and period with its ``coverage`` fraction and the
            count of quarantined source rows, so a report can footnote which
            numbers rest on incomplete data.
        """
        ...

    def delete_period(self, plant_id: UUID, window: TimeWindow) -> int:
        """Remove every stored KPI value for a plant and period.

        Used when a plant is found to have been mis-configured and its computed
        history must be withdrawn rather than corrected in place.

        Args:
            plant_id: the plant to clear.
            window: the UTC period to clear.

        Returns:
            The number of rows deleted.

        Raises:
            RepositoryError: if the delete fails.
        """
        ...


__all__ = ["KPIRepository", "KPIValue"]
