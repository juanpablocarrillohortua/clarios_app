"""Port for persisting and querying Bronze and Silver readings.

Layer: ports

Responsibility:
    Declares how the application writes raw records into Bronze and validated
    records into Silver, and how it reads them back. The two layers have
    deliberately asymmetric contracts: Bronze is append-only and deduplicated by
    content hash, while Silver is upsertable on ``(meter_id, ts_utc)`` because
    re-processing a corrected source must be able to supersede an earlier value.
    Neither method validates data - validation happens in
    :mod:`energy.pipelines.tasks.validate` against the Pandera contracts before a
    frame ever reaches a repository. This protocol mentions no SQL, no session,
    and no table.

Depends on:
    ``energy.domain``, standard library typing only.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    import polars as pl

    from energy.domain.value_objects import TimeWindow
    from energy.ports.energy_source import SourceBatch


@dataclass(frozen=True, slots=True)
class WriteResult:
    """Outcome of a write into Bronze or Silver.

    Attributes:
        rows_received: how many rows the caller submitted.
        rows_written: how many were newly persisted.
        rows_skipped: how many were rejected as already present. In Bronze this
            counts content-hash duplicates; a re-uploaded file is a no-op, not
            an error.
        rows_superseded: how many existing Silver rows a corrected value
            replaced. Always zero for Bronze, which is immutable.
        run_id: the ingestion run this write belongs to.
    """

    rows_received: int
    rows_written: int
    rows_skipped: int
    rows_superseded: int
    run_id: UUID


@runtime_checkable
class ReadingRepository(Protocol):
    """Interface for reading and writing meter readings across Bronze and Silver."""

    def write_bronze(self, batch: SourceBatch, run_id: UUID) -> WriteResult:
        """Append a source batch to Bronze with full provenance.

        Every row is stamped with the provenance columns listed in
        :data:`energy.core.constants.PROVENANCE_COLUMNS`. Rows whose content hash
        already exists are skipped, which makes re-running a failed flow safe.

        Args:
            batch: the raw batch as returned by an
                :class:`~energy.ports.energy_source.EnergySource`.
            run_id: the ingestion run to attribute these rows to.

        Returns:
            A :class:`WriteResult` with ``rows_superseded`` always zero.

        Raises:
            RepositoryError: if the write fails. The whole batch is rolled back;
                partial Bronze batches are never committed.
        """
        ...

    def upsert_silver(self, frame: pl.DataFrame, run_id: UUID) -> WriteResult:
        """Insert or supersede validated readings in Silver.

        Conflict key is ``(meter_id, ts_utc)``. An incoming row supersedes an
        existing one only when its source has equal or better priority; a
        lower-priority source must never overwrite a higher-priority value that
        is already present.

        Args:
            frame: validated readings conforming to the Silver Pandera contract.
            run_id: the pipeline run to attribute the write to.

        Returns:
            A :class:`WriteResult` distinguishing new rows from superseded ones.

        Raises:
            SchemaValidationError: if the frame does not match the Silver contract.
            RepositoryError: if the write fails.
        """
        ...

    def read_silver(
        self,
        plant_id: UUID,
        window: TimeWindow,
        meter_ids: tuple[UUID, ...] | None = None,
    ) -> pl.DataFrame:
        """Read validated readings for a plant over a period.

        Args:
            plant_id: the plant to read.
            window: the UTC period to read, half-open.
            meter_ids: restrict to these meters; ``None`` reads every meter of
                the plant.

        Returns:
            A frame on the canonical grid conforming to the Silver contract.
            Intervals with no data are absent rather than zero-filled, so the
            caller can tell missing from zero.

        Raises:
            RepositoryError: if the query fails.
        """
        ...

    def read_bronze_raw(self, run_id: UUID) -> pl.DataFrame:
        """Read back the exact rows a given ingestion run wrote.

        The audit path: when a Gold number is disputed, this returns the raw
        records behind it, untransformed.

        Args:
            run_id: the ingestion run to read.

        Returns:
            The Bronze rows for that run, including every provenance column.

        Raises:
            EntityNotFoundError: if the run id is unknown.
        """
        ...

    def latest_timestamp(self, plant_id: UUID, source_key: str) -> datetime | None:
        """Return the newest Silver timestamp for a plant and source.

        Used to compute the next incremental window and to report freshness.

        Args:
            plant_id: the plant to inspect.
            source_key: the source system to inspect.

        Returns:
            The newest ``ts_utc`` present, timezone-aware UTC, or ``None`` when
            the source has never delivered data for this plant.
        """
        ...

    def content_hash_exists(self, content_hash: str) -> bool:
        """Return whether a payload has already been ingested.

        Args:
            content_hash: SHA-256 hex digest of a raw payload.

        Returns:
            ``True`` when Bronze already holds rows with this hash, which lets an
            upload be rejected as a duplicate before it is parsed.
        """
        ...


__all__ = ["ReadingRepository", "WriteResult"]
