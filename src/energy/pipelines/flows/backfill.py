"""Flow replaying historical data through the medallion layers.

Layer: pipelines

Responsibility:
    Loads or reloads a long historical period - commissioning a new plant,
    recovering from an outage, or re-deriving Silver after a consolidation rule
    changes. It differs from the nightly flow in three ways that matter: it works
    in bounded chunks so memory stays flat over years of data, it is
    rate-limited so a backfill cannot starve the live pipeline of database
    connections, and it is resumable, recording progress per chunk so an
    interrupted multi-day backfill restarts where it stopped rather than from the
    beginning.

Depends on:
    ``energy.pipelines.tasks``, ``energy.pipelines.watermarks``,
    ``energy.adapters.registry``, ``energy.core``, ``prefect``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from datetime import datetime

    from energy.domain.value_objects import TimeWindow


def backfill(
    plant_code: str,
    start: datetime,
    end: datetime,
    source_keys: Sequence[str] | None = None,
    chunk_days: int = 7,
    force: bool = False,
) -> dict[str, object]:
    """Replay a historical period for one plant.

    Args:
        plant_code: the plant to backfill.
        start: period start, inclusive, timezone-aware UTC.
        end: period end, exclusive, timezone-aware UTC.
        source_keys: sources to replay; ``None`` replays every configured source.
        chunk_days: size of each processing chunk. Larger chunks are faster but
            hold more memory and lock Gold periods for longer.
        force: re-ingest payloads whose content hash is already in Bronze.
            Defaults to off, so a re-run after a partial failure skips what
            already landed instead of duplicating it.

    Returns:
        Per-chunk outcomes, total rows per layer, and any chunks that failed.

    Raises:
        InvalidTimeWindowError: if the period is inverted or unaligned.
        EntityNotFoundError: if the plant has no configured sources.
    """
    raise NotImplementedError("Phase 3: historical backfill flow")


def chunk_windows(start: datetime, end: datetime, chunk_days: int) -> Iterator[TimeWindow]:
    """Divide a long period into processing chunks.

    Args:
        start: period start, inclusive and grid-aligned.
        end: period end, exclusive and grid-aligned.
        chunk_days: target chunk length in days.

    Yields:
        Consecutive windows tiling the period. The final chunk may be shorter.

    Raises:
        InvalidTimeWindowError: if the bounds are unaligned or inverted.
    """
    raise NotImplementedError("Phase 3: backfill chunking")


def resume_point(plant_code: str, start: datetime, end: datetime) -> datetime:
    """Return where an interrupted backfill should restart.

    Args:
        plant_code: the plant being backfilled.
        start: the original period start.
        end: the original period end.

    Returns:
        The start of the first chunk not yet completed, or ``start`` when no
        progress was recorded.
    """
    raise NotImplementedError("Phase 3: backfill resumption")


__all__ = ["backfill", "chunk_windows", "resume_point"]
