"""Incremental ingestion state.

Layer: pipelines

Responsibility:
    Tracks how far each (plant, source) pair has been ingested, so a scheduled
    run reads only what is new. The subtlety this module exists to handle is that
    the naive watermark - "resume from the newest timestamp we hold" - is wrong
    for sources that deliver out of order or revise history: it permanently skips
    anything that arrives late. Watermarks here are therefore held back by a
    configured lateness allowance, and advancing one is an explicit, audited
    operation rather than a side effect of a successful read.

Depends on:
    ``energy.ports.reading_repository``, ``energy.domain.exceptions``,
    ``energy.core``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from sqlalchemy.orm import Session

    from energy.domain.value_objects import TimeWindow


@dataclass(frozen=True, slots=True)
class Watermark:
    """How far one source has been ingested for one plant.

    Attributes:
        plant_code: the plant.
        source_key: the source.
        position: the high-water mark, UTC. Everything strictly before this is
            considered fully ingested.
        lateness_allowance: how far behind the newest observed timestamp the
            position is deliberately held, to absorb out-of-order arrivals.
        updated_at: when the watermark last advanced, UTC.
        run_id: the run that last advanced it.
    """

    plant_code: str
    source_key: str
    position: datetime
    lateness_allowance: timedelta
    updated_at: datetime
    run_id: str


def read_watermark(session: Session, plant_code: str, source_key: str) -> Watermark | None:
    """Read the current watermark for a plant and source.

    Args:
        session: an open session.
        plant_code: the plant to read.
        source_key: the source to read.

    Returns:
        The stored watermark, or ``None`` when the pair has never been ingested.
    """
    raise NotImplementedError("Phase 3: watermark read")


def next_window(
    watermark: Watermark | None,
    now: datetime,
    default_lookback: timedelta,
) -> TimeWindow:
    """Compute the period the next run should read.

    Args:
        watermark: the current watermark, or ``None`` for a first run.
        now: the current instant, UTC.
        default_lookback: how far back a first run should reach when no watermark
            exists.

    Returns:
        A grid-aligned window starting at the watermark position - not at the
        newest ingested timestamp - so late arrivals within the lateness
        allowance are re-read rather than skipped.

    Raises:
        WatermarkError: if the watermark lies in the future, which means a clock
            skew or a bad manual edit and must not be silently accepted.
    """
    raise NotImplementedError("Phase 3: next window computation")


def advance(
    session: Session,
    watermark: Watermark,
    newest_seen: datetime,
    run_id: str,
) -> Watermark:
    """Move a watermark forward after a successful read.

    Args:
        session: an open session.
        watermark: the current watermark.
        newest_seen: the newest timestamp the run actually observed, UTC.
        run_id: the run advancing it.

    Returns:
        The new watermark, positioned at ``newest_seen`` minus the lateness
        allowance.

    Raises:
        WatermarkError: if the new position would move backwards, which would
            cause data to be reprocessed indefinitely.
    """
    raise NotImplementedError("Phase 3: watermark advance")


def reset(session: Session, plant_code: str, source_key: str, position: datetime) -> Watermark:
    """Force a watermark to an explicit position.

    The deliberate escape hatch for re-ingesting a period after a bug is fixed.
    Always audited, because it causes data to be reprocessed.

    Args:
        session: an open session.
        plant_code: the plant to reset.
        source_key: the source to reset.
        position: the position to set, UTC and grid-aligned.

    Returns:
        The reset watermark.

    Raises:
        InvalidTimeWindowError: if ``position`` is not grid-aligned.
    """
    raise NotImplementedError("Phase 3: watermark reset")


__all__ = ["Watermark", "advance", "next_window", "read_watermark", "reset"]
