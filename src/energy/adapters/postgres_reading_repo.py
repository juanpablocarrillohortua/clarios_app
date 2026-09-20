"""PostgreSQL implementation of the reading repository port.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.reading_repository.ReadingRepository`
    against PostgreSQL 16 with TimescaleDB. It owns the SQL that makes the two
    medallion layers behave differently: Bronze writes are plain appends with an
    ``ON CONFLICT DO NOTHING`` on the content hash, while Silver writes are
    upserts on ``(meter_id, ts_utc)`` guarded by source priority so a
    lower-priority source cannot clobber a better value. Bulk writes go through
    ``COPY`` rather than row-by-row inserts, because a backfill moves tens of
    millions of rows. No business rules live here.

Depends on:
    ``energy.ports.reading_repository``, ``energy.db.session``,
    ``energy.db.models``, ``energy.domain.exceptions``, ``sqlalchemy``, ``polars``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from energy.ports.reading_repository import WriteResult

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    import polars as pl
    from sqlalchemy.orm import Session

    from energy.domain.value_objects import TimeWindow
    from energy.ports.energy_source import SourceBatch


class PostgresReadingRepository:
    """Reading persistence backed by PostgreSQL and TimescaleDB.

    Args:
        session: an open session. The repository never opens or commits its own
            transaction - the caller owns the transaction boundary, so one flow
            step writing to several tables either lands completely or not at all.
    """

    def __init__(self, session: Session) -> None:
        raise NotImplementedError("Phase 3: repository construction")

    def write_bronze(self, batch: SourceBatch, run_id: UUID) -> WriteResult:
        """Append a source batch to ``bronze.raw_readings`` via COPY.

        Args:
            batch: the raw batch from an energy source.
            run_id: the ingestion run to attribute the rows to.

        Returns:
            A :class:`~energy.ports.reading_repository.WriteResult`; rows whose
            content hash is already present count as skipped.

        Raises:
            RepositoryError: if the write fails; nothing is committed.
        """
        raise NotImplementedError("Phase 3: Bronze bulk append")

    def upsert_silver(self, frame: pl.DataFrame, run_id: UUID) -> WriteResult:
        """Insert or supersede rows in ``silver.readings``.

        Uses a staging table plus a single ``INSERT ... ON CONFLICT DO UPDATE``
        with a ``WHERE`` clause comparing source priority, so the priority rule
        is enforced by the database rather than by a read-modify-write race.

        Args:
            frame: validated readings matching the Silver Pandera contract.
            run_id: the run to attribute the write to.

        Returns:
            A write result distinguishing new rows from superseded ones.

        Raises:
            SchemaValidationError: if the frame does not match the contract.
            RepositoryError: if the write fails.
        """
        raise NotImplementedError("Phase 3: Silver priority-guarded upsert")

    def read_silver(
        self,
        plant_id: UUID,
        window: TimeWindow,
        meter_ids: tuple[UUID, ...] | None = None,
    ) -> pl.DataFrame:
        """Read validated readings for a plant over a period.

        Args:
            plant_id: the plant to read.
            window: the half-open UTC period.
            meter_ids: restrict to these meters; ``None`` reads all.

        Returns:
            A frame on the canonical grid; missing intervals are absent, not zero.

        Raises:
            RepositoryError: if the query fails.
        """
        raise NotImplementedError("Phase 3: Silver range read")

    def read_bronze_raw(self, run_id: UUID) -> pl.DataFrame:
        """Read back the exact rows one ingestion run wrote.

        Args:
            run_id: the run to read.

        Returns:
            The Bronze rows including every provenance column.

        Raises:
            EntityNotFoundError: if the run id is unknown.
        """
        raise NotImplementedError("Phase 3: Bronze audit read")

    def latest_timestamp(self, plant_id: UUID, source_key: str) -> datetime | None:
        """Return the newest Silver timestamp for a plant and source.

        Args:
            plant_id: the plant to inspect.
            source_key: the source to inspect.

        Returns:
            The newest ``ts_utc``, or ``None`` when nothing has been ingested.
        """
        raise NotImplementedError("Phase 3: watermark query")

    def content_hash_exists(self, content_hash: str) -> bool:
        """Return whether a payload has already been ingested.

        Args:
            content_hash: SHA-256 hex digest of a raw payload.

        Returns:
            ``True`` when Bronze already holds rows with this hash.
        """
        raise NotImplementedError("Phase 3: content hash lookup")


__all__ = ["PostgresReadingRepository"]
