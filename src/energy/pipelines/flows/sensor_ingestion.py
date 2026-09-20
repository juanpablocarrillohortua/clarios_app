"""Flow promoting pushed sensor readings from Bronze into Silver and Gold.

Layer: pipelines

Responsibility:
    Runs on a short schedule to promote the readings that edge devices push into
    Bronze through :mod:`energy.api.routers.ingest`. The API writes Bronze
    synchronously and returns; this flow does the expensive validation,
    resampling and KPI recomputation out of band. Its central concern is the
    trade-off between latency and completeness: a device may deliver a late
    reading for an interval that has already been promoted, so the flow
    reprocesses a short trailing window rather than only the newest data, and
    relies on Gold period replacement to make that idempotent.

Depends on:
    ``energy.pipelines.tasks``, ``energy.pipelines.watermarks``,
    ``energy.services``, ``energy.core``, ``prefect``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def sensor_ingestion(
    plant_codes: Sequence[str] | None = None,
    trailing_hours: int = 6,
) -> dict[str, object]:
    """Promote recently pushed readings through the medallion layers.

    Args:
        plant_codes: plants to process; ``None`` processes every plant with a
            push-based source.
        trailing_hours: how far back to reprocess, absorbing late arrivals.

    Returns:
        Per-plant row counts and the new watermark for each source.

    Raises:
        PipelineError: if the watermark cannot be advanced, which would cause the
            next run to reprocess the same window indefinitely.
    """
    raise NotImplementedError("Phase 3: sensor promotion flow")


def detect_stale_sources(max_silence_minutes: int = 60) -> list[dict[str, object]]:
    """Identify push sources that have stopped delivering.

    A device that stops pushing produces no error anywhere - it simply goes
    quiet, and the gap is only noticed when someone looks at a chart. This check
    turns that silence into an explicit alert.

    Args:
        max_silence_minutes: how long a source may be silent before it is
            considered stale.

    Returns:
        One entry per stale source with its plant, source key, last-seen
        timestamp and silence duration, ready to hand to a
        :class:`~energy.ports.notifier.Notifier`.
    """
    raise NotImplementedError("Phase 3: stale source detection")


__all__ = ["detect_stale_sources", "sensor_ingestion"]
