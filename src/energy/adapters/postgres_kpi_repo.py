"""PostgreSQL implementation of the KPI repository port.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.kpi_repository.KPIRepository` against the
    Gold schema. Its defining behaviour is period replacement: writing KPIs for a
    period deletes the existing rows for that exact scope and inserts the new set
    inside one transaction, so a recomputation that yields fewer values does not
    leave stale rows behind. Read queries are written to hit the TimescaleDB
    chunk-exclusion path, so a dashboard asking for one month does not scan a
    decade.

Depends on:
    ``energy.ports.kpi_repository``, ``energy.db.session``, ``energy.db.models``,
    ``energy.domain.exceptions``, ``sqlalchemy``, ``polars``.

Implemented in:
    Phase 4 - KPI computation and Gold layer
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from energy.ports.kpi_repository import KPIValue

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    import polars as pl
    from sqlalchemy.orm import Session

    from energy.domain.value_objects import TimeWindow


class PostgresKPIRepository:
    """Gold-layer KPI persistence backed by PostgreSQL and TimescaleDB.

    Args:
        session: an open session. The caller owns the transaction boundary.
    """

    def __init__(self, session: Session) -> None:
        raise NotImplementedError("Phase 4: repository construction")

    def replace_period(
        self,
        plant_id: UUID,
        window: TimeWindow,
        granularity: str,
        values: Sequence[KPIValue],
    ) -> int:
        """Atomically replace every KPI value for a scope.

        Args:
            plant_id: the plant whose values are replaced.
            window: the half-open UTC period to replace.
            granularity: which granularity is replaced.
            values: the complete new set for that scope.

        Returns:
            The number of rows written.

        Raises:
            RepositoryError: if the transaction fails; nothing is committed.
        """
        raise NotImplementedError("Phase 4: Gold period replacement")

    def query(
        self,
        plant_ids: Sequence[UUID],
        kpi_keys: Sequence[str],
        window: TimeWindow,
        granularity: str,
    ) -> pl.DataFrame:
        """Read KPI values for dashboards and API responses.

        Args:
            plant_ids: plants to include; empty means all.
            kpi_keys: KPIs to include; empty means all.
            window: the half-open UTC period.
            granularity: the granularity to read.

        Returns:
            A long-format frame, one row per plant, KPI and period.

        Raises:
            UnknownKPIError: if a requested key is not configured.
            RepositoryError: if the query fails.
        """
        raise NotImplementedError("Phase 4: Gold KPI query")

    def latest(self, plant_id: UUID, kpi_key: str, granularity: str) -> KPIValue | None:
        """Return the most recent stored value of one KPI.

        Args:
            plant_id: the plant to read.
            kpi_key: which KPI.
            granularity: which granularity.

        Returns:
            The newest value, or ``None`` if never computed.
        """
        raise NotImplementedError("Phase 4: latest KPI lookup")

    def coverage_report(self, plant_id: UUID, window: TimeWindow) -> pl.DataFrame:
        """Summarise data coverage behind a period's KPIs.

        Args:
            plant_id: the plant to report on.
            window: the UTC period to summarise.

        Returns:
            Per-KPI coverage fractions and quarantined row counts.
        """
        raise NotImplementedError("Phase 4: coverage aggregation")

    def delete_period(self, plant_id: UUID, window: TimeWindow) -> int:
        """Remove every stored KPI value for a plant and period.

        Args:
            plant_id: the plant to clear.
            window: the UTC period to clear.

        Returns:
            The number of rows deleted.

        Raises:
            RepositoryError: if the delete fails.
        """
        raise NotImplementedError("Phase 4: Gold period deletion")


__all__ = ["PostgresKPIRepository"]
