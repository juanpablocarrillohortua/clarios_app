"""Port for any system that can supply energy, gas, or production data.

Layer: ports

Responsibility:
    Declares the single interface every data source must satisfy, whether it is
    an Excel workbook dropped on a share, a nightly ERP extract, or a live plant
    historian. The application asks a source for raw records over a time window
    and receives rows plus the provenance needed to write them to Bronze; it
    never learns how they were obtained. This module must never import a driver,
    an SDK, or a file-format library - those belong in
    :mod:`energy.adapters`. Adding a seventh or eighth source means writing a new
    adapter against this protocol and registering it, with no change to any
    pipeline.

Depends on:
    ``energy.domain``, standard library typing only.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime, timedelta

    import polars as pl

    from energy.domain.value_objects import TimeWindow


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    """Static identity and capability declaration of a configured source.

    Built from one entry in ``config/sources.yaml``.

    Attributes:
        key: unique source key, used as ``bronze.raw_readings.source_system``.
        plant_code: the plant this source reports for.
        kind: ``"excel"``, ``"erp"``, or ``"historian"``.
        native_frequency: the cadence the source natively reports at. Drives the
            resampling policy applied on the way to Silver.
        timezone: IANA timezone the source's timestamps are expressed in. Sources
            that already report UTC declare ``"UTC"``.
        priority: tie-break rank when several sources cover the same meter and
            interval. Lower wins.
        supports_incremental: whether the source can be queried for a window, or
            must be read in full every time.
    """

    key: str
    plant_code: str
    kind: str
    native_frequency: timedelta
    timezone: str
    priority: int
    supports_incremental: bool


@dataclass(frozen=True, slots=True)
class SourceBatch:
    """One pull from a source, carrying the data and its provenance.

    Attributes:
        descriptor: which source produced this batch.
        frame: the raw records, exactly as read, with no renaming, no type
            coercion and no unit conversion applied. Bronze stores what arrived.
        source_uri: where the data came from, precisely enough to fetch it again:
            an object-storage key, a file path, or a query identifier.
        content_hash: SHA-256 of the raw payload, used to detect a re-upload of
            an already-ingested file and skip it.
        extracted_at: when the pull happened, timezone-aware UTC.
        window: the period the caller requested, or ``None`` for a full read.
        row_count: number of records in ``frame``, recorded separately so an
            empty batch is distinguishable from a failed one.
    """

    descriptor: SourceDescriptor
    frame: pl.DataFrame
    source_uri: str
    content_hash: str
    extracted_at: datetime
    window: TimeWindow | None
    row_count: int


@runtime_checkable
class EnergySource(Protocol):
    """Interface every energy, gas, or production data source implements."""

    @property
    def descriptor(self) -> SourceDescriptor:
        """Return this source's static identity and capabilities."""
        ...

    def health_check(self) -> bool:
        """Confirm the source is reachable and credentials are valid.

        Called before a pipeline run so an unreachable source fails fast with a
        clear cause instead of surfacing as an empty batch.

        Returns:
            ``True`` when the source answers.

        Raises:
            SourceUnavailableError: when the source cannot be reached or
                authentication fails.
        """
        ...

    def fetch(self, window: TimeWindow | None = None) -> SourceBatch:
        """Read records from the source.

        Args:
            window: the period to read, in UTC. Sources that declare
                ``supports_incremental`` must honour it; the rest may ignore it
                and return everything, leaving the caller to filter. ``None``
                requests a full read.

        Returns:
            A :class:`SourceBatch` with the raw frame and its provenance. An
            empty frame is a valid result and must not raise.

        Raises:
            SourceUnavailableError: if the source cannot be reached.
            UnsupportedFileFormatError: if the payload is not in a readable form.
        """
        ...

    def fetch_chunks(
        self,
        window: TimeWindow | None = None,
        chunk_size: int = 100_000,
    ) -> Iterator[SourceBatch]:
        """Read records in bounded chunks for backfills.

        A multi-year backfill from a historian will not fit in memory as one
        frame, so this is the path :mod:`energy.pipelines.flows.backfill` uses.

        Args:
            window: the period to read, in UTC.
            chunk_size: maximum rows per yielded batch.

        Yields:
            Successive :class:`SourceBatch` instances, each with its own
            ``content_hash`` covering only that chunk.

        Raises:
            SourceUnavailableError: if the source becomes unreachable mid-read.
        """
        ...


__all__ = ["EnergySource", "SourceBatch", "SourceDescriptor"]
