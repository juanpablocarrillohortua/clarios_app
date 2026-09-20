"""Task merging seven sources onto one canonical grid.

Layer: pipelines

Responsibility:
    The hardest step in the platform, and the reason Silver exists. Seven source
    systems report the same plant at different cadences, in different timezones,
    with overlapping coverage and disagreeing values. This task resamples each
    source onto the canonical hourly UTC grid, then resolves every remaining
    conflict by configured source priority, recording each decision in
    ``silver.consolidation_audit`` so an overridden value is never silently
    discarded. It also distinguishes a genuine zero from an absence: an interval
    with no source coverage is emitted as ``missing``, never as zero, because a
    zero would quietly deflate every average computed over it.

Depends on:
    ``energy.domain.time_grid``, ``energy.schemas.silver``, ``energy.core``,
    ``polars``, ``prefect``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    import polars as pl

    from energy.domain.value_objects import TimeWindow

#: How a source's native cadence is mapped onto the canonical hourly grid.
ResampleRule = Literal["sum", "mean", "last", "first", "max", "time_weighted"]


@dataclass(frozen=True, slots=True)
class ConsolidationResult:
    """Outcome of consolidating several sources for one plant and window.

    Attributes:
        readings: the winning readings on the canonical grid.
        audit: one row per interval where sources disagreed, naming the winner,
            the losers, and the relative divergence between them.
        gaps: intervals no source covered, emitted as ``missing`` readings.
        conflict_count: how many intervals had more than one candidate.
        max_divergence_pct: the largest relative disagreement observed. A high
            value means two meters are measuring different things, or one is
            misconfigured - it is a data-quality alert, not a resolved conflict.
    """

    readings: pl.DataFrame
    audit: pl.DataFrame
    gaps: pl.DataFrame
    conflict_count: int
    max_divergence_pct: float


def resample_to_grid(
    frame: pl.DataFrame,
    rule: ResampleRule,
    source_timezone: str,
) -> pl.DataFrame:
    """Resample one source's readings onto the canonical hourly UTC grid.

    Timezone conversion happens before resampling, not after. Resampling in local
    time and converting afterwards misplaces every interval across a
    daylight-saving transition, which is exactly when a plant's consumption
    pattern looks most anomalous and is scrutinised most closely.

    Args:
        frame: one source's readings at its native cadence.
        rule: how to aggregate sub-hourly values. ``sum`` for consumption
            counters, ``time_weighted`` for instantaneous measurements,
            ``last`` for cumulative registers.
        source_timezone: IANA timezone the source's timestamps are in.

    Returns:
        Readings on the canonical grid, with an interval's coverage recorded so
        partially covered intervals can be flagged rather than silently
        understated.

    Raises:
        ConfigurationError: if the source cadence does not divide the canonical
            hour evenly.
    """
    raise NotImplementedError("Phase 3: source resampling")


def consolidate(
    per_source: Mapping[str, pl.DataFrame],
    priorities: Mapping[str, int],
    window: TimeWindow,
    run_id: UUID,
    divergence_tolerance_pct: float = 2.0,
) -> ConsolidationResult:
    """Merge every source for one plant into a single set of readings.

    Args:
        per_source: already-resampled readings keyed by source key.
        priorities: source priority, lower wins, from ``config/sources.yaml``.
        window: the period being consolidated, used to detect gaps.
        run_id: the run to stamp on the audit rows.
        divergence_tolerance_pct: how far two sources may disagree before the
            winning reading is downgraded to ``suspect``. Agreement within
            tolerance is normal measurement noise; beyond it, the value is
            reported but flagged.

    Returns:
        A :class:`ConsolidationResult` with the winning readings, the audit
        trail, and the uncovered intervals.

    Raises:
        ConfigurationError: if a source in ``per_source`` has no configured
            priority, which would make conflict resolution non-deterministic.
    """
    raise NotImplementedError("Phase 3: multi-source consolidation")


def detect_gaps(readings: pl.DataFrame, window: TimeWindow) -> pl.DataFrame:
    """Emit explicit ``missing`` rows for intervals no source covered.

    Args:
        readings: the consolidated readings.
        window: the period that should be fully covered.

    Returns:
        One row per uncovered meter and interval, flagged ``missing`` with a null
        value - never zero.
    """
    raise NotImplementedError("Phase 3: gap materialisation")


def source_order(priorities: Mapping[str, int], available: Sequence[str]) -> list[str]:
    """Return the available sources in conflict-resolution order.

    Args:
        priorities: configured priority per source key.
        available: sources that actually returned data for this window.

    Returns:
        Source keys sorted by ascending priority.

    Raises:
        ConfigurationError: if two available sources share a priority, which
            would make the winner depend on dictionary ordering.
    """
    raise NotImplementedError("Phase 3: priority ordering")


__all__ = [
    "ConsolidationResult",
    "ResampleRule",
    "consolidate",
    "detect_gaps",
    "resample_to_grid",
    "source_order",
]
