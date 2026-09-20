"""Adapter reading production and product-mix data from the ERP.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.energy_source.EnergySource` for the nightly
    ERP extract that supplies the *denominator* of every efficiency KPI: units
    produced, per product, per period. Energy without production is just a bill;
    this adapter is what makes Specific Energy Consumption computable. It reads
    the extract, preserves the product breakdown rather than pre-summing it (the
    mix is needed for equivalent-unit normalisation), and records the ERP
    document reference so a disputed figure can be traced to a production order.
    It never applies equivalence factors - that is domain logic.

Depends on:
    ``energy.ports.energy_source``, ``energy.domain.exceptions``,
    ``energy.core``, ``polars``, ``sqlalchemy``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from energy.ports.energy_source import SourceBatch, SourceDescriptor

if TYPE_CHECKING:
    from collections.abc import Iterator

    from energy.domain.value_objects import TimeWindow


@dataclass(frozen=True, slots=True)
class ERPConnection:
    """Connection parameters for one ERP extract endpoint.

    Attributes:
        dsn: connection string for the ERP reporting replica. Always a replica:
            the platform must never query a production ERP instance directly.
        query_name: named query in ``config/sources.yaml`` to execute.
        batch_column: column used to page through large results.
        timeout_seconds: statement timeout; an ERP query that outruns it is
            cancelled rather than left holding a cursor overnight.
    """

    dsn: str
    query_name: str
    batch_column: str
    timeout_seconds: int


class ERPSource:
    """Reads production quantities from the ERP reporting replica.

    Args:
        descriptor: the configured source identity.
        connection: how to reach the ERP and which query to run.
    """

    def __init__(self, descriptor: SourceDescriptor, connection: ERPConnection) -> None:
        raise NotImplementedError("Phase 3: ERP source construction")

    @property
    def descriptor(self) -> SourceDescriptor:
        """Return this source's static identity and capabilities."""
        raise NotImplementedError("Phase 3: descriptor accessor")

    def health_check(self) -> bool:
        """Confirm the ERP replica is reachable and the query is valid.

        Returns:
            ``True`` when the connection opens and the query plans successfully.

        Raises:
            SourceUnavailableError: if the replica is unreachable or credentials
                are rejected.
        """
        raise NotImplementedError("Phase 3: ERP reachability check")

    def fetch(self, window: TimeWindow | None = None) -> SourceBatch:
        """Read production records for a period.

        The ERP is incrementally queryable, so ``window`` is pushed down into the
        query rather than applied afterwards.

        Args:
            window: the period to read, in UTC. Converted to the ERP's own
                timezone before being bound into the query, because the ERP
                reports in plant-local business days.

        Returns:
            A batch with one row per product, plant and period, carrying the
            quantity, its unit, and the ERP document reference.

        Raises:
            SourceUnavailableError: if the replica becomes unreachable.
        """
        raise NotImplementedError("Phase 3: ERP production extract")

    def fetch_chunks(
        self,
        window: TimeWindow | None = None,
        chunk_size: int = 100_000,
    ) -> Iterator[SourceBatch]:
        """Page through a large ERP result set.

        Args:
            window: the period to read, in UTC.
            chunk_size: maximum rows per yielded batch.

        Yields:
            Successive batches ordered by ``connection.batch_column``.

        Raises:
            SourceUnavailableError: if the replica becomes unreachable mid-read.
        """
        raise NotImplementedError("Phase 3: chunked ERP extract")


__all__ = ["ERPConnection", "ERPSource"]
