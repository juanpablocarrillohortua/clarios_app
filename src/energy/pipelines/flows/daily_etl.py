"""Nightly flow promoting Bronze to Silver to Gold for every plant.

Layer: pipelines

Responsibility:
    The platform's main scheduled job. For each plant it pulls every configured
    source, lands the raw rows in Bronze, validates and consolidates them into
    Silver, then recomputes the Gold KPIs for the affected period. Plants are
    processed independently and concurrently, which is the flow's key design
    decision: one plant's unreachable historian must not prevent the other six
    from loading. The flow owns scheduling, concurrency and retries; every piece
    of actual work lives in :mod:`energy.pipelines.tasks`.

Depends on:
    ``energy.pipelines.tasks``, ``energy.adapters.registry``,
    ``energy.services``, ``energy.core``, ``prefect``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from energy.domain.value_objects import TimeWindow


def daily_etl(
    target_date: datetime | None = None,
    plant_codes: Sequence[str] | None = None,
    lookback_hours: int = 48,
) -> dict[str, object]:
    """Run the nightly extract, validate, consolidate and compute cycle.

    The window deliberately extends ``lookback_hours`` into the past rather than
    covering only the previous day. Sources routinely deliver late or revise
    already-delivered values, and re-processing a rolling window is what lets
    those corrections land. Because Gold is replaced per period rather than
    appended to, reprocessing is idempotent.

    Args:
        target_date: the day to process, UTC. Defaults to the current day.
        plant_codes: plants to process; ``None`` processes every active plant.
        lookback_hours: how far back from ``target_date`` to reprocess.

    Returns:
        A per-plant summary: rows landed in each layer, rows quarantined, and the
        per-plant outcome. Plants that failed are reported rather than raising,
        so the summary always covers every plant attempted.

    Raises:
        PipelineError: only if *every* plant failed, which indicates a platform
            fault rather than a source fault.
    """
    raise NotImplementedError("Phase 3: nightly ETL flow")


def process_plant(
    plant_code: str,
    window: TimeWindow,
    run_id: UUID,
) -> dict[str, int]:
    """Run the full medallion promotion for one plant over one window.

    Args:
        plant_code: the plant to process.
        window: the half-open UTC period to process.
        run_id: the run to attribute every write to.

    Returns:
        Row counts per layer plus the quarantined count.

    Raises:
        SourceUnavailableError: if every source for the plant is unreachable.
    """
    raise NotImplementedError("Phase 3: per-plant promotion subflow")


__all__ = ["daily_etl", "process_plant"]
