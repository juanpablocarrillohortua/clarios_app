"""Task pulling raw records from a source into Bronze.

Layer: pipelines

Responsibility:
    The Bronze-writing step. Asks an :class:`~energy.ports.energy_source.EnergySource`
    for records, stamps provenance, and appends them. It deliberately does
    nothing else: no type coercion, no unit conversion, no filtering, no
    deduplication beyond the content-hash check. Any transformation applied here
    would be applied to data before it was preserved, and would therefore be
    unauditable. Retries are safe because Bronze writes are idempotent on the
    content hash.

Depends on:
    ``energy.ports.energy_source``, ``energy.ports.reading_repository``,
    ``energy.schemas.bronze``, ``energy.core``, ``prefect``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from energy.domain.value_objects import TimeWindow
    from energy.ports.energy_source import EnergySource
    from energy.ports.reading_repository import ReadingRepository, WriteResult


def extract_to_bronze(
    source: EnergySource,
    repository: ReadingRepository,
    window: TimeWindow | None,
    run_id: UUID,
) -> WriteResult:
    """Pull records from a source and append them to Bronze.

    Args:
        source: the adapter to read from.
        repository: the reading repository to write through.
        window: the period to read; ``None`` for a full read.
        run_id: the run to attribute the rows to.

    Returns:
        The write result, with duplicates counted as skipped rather than failed.

    Raises:
        SourceUnavailableError: if the source cannot be reached. Retried by
            Prefect with backoff; a persistent failure fails only this plant.
        SchemaValidationError: if the batch lacks its provenance envelope, which
            is an adapter bug and is not retried.
    """
    raise NotImplementedError("Phase 3: Bronze extraction task")


def extract_chunked(
    source: EnergySource,
    repository: ReadingRepository,
    window: TimeWindow,
    run_id: UUID,
    chunk_size: int = 100_000,
) -> WriteResult:
    """Pull a large period in chunks, appending each as it arrives.

    Each chunk is committed independently so an interrupted extraction leaves the
    chunks it completed in Bronze rather than discarding hours of transfer.

    Args:
        source: the adapter to read from.
        repository: the reading repository to write through.
        window: the period to read.
        run_id: the run to attribute the rows to.
        chunk_size: maximum rows per chunk.

    Returns:
        The aggregate write result across every chunk.

    Raises:
        SourceUnavailableError: if the source becomes unreachable mid-read.
    """
    raise NotImplementedError("Phase 3: chunked Bronze extraction task")


__all__ = ["extract_chunked", "extract_to_bronze"]
