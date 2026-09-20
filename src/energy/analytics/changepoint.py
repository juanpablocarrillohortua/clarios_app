"""Changepoint detection in consumption behaviour.

Layer: analytics

Responsibility:
    Finds the moments when a plant's consumption behaviour changed persistently,
    as opposed to the transient excursions
    :mod:`energy.analytics.anomaly` reports. The distinction is what makes this a
    separate module: a spike is an incident to investigate, whereas a sustained
    level shift means the plant is now a different system - new equipment, a
    changed product mix, a failed control loop - and every model fitted before it
    is now describing something that no longer exists. Detected changepoints are
    therefore what trigger baseline re-fitting.

Depends on:
    ``energy.domain``, ``energy.core``, ``polars``, ``statsmodels``,
    ``scikit-learn`` (optional ``analytics`` extra).

Implemented in:
    Phase 5 - Analytics, alerting and reporting
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import datetime

    import polars as pl

#: What kind of change was detected.
ChangeKind = Literal["level", "trend", "variance"]


@dataclass(frozen=True, slots=True)
class Changepoint:
    """One detected persistent change in behaviour.

    Attributes:
        plant_code: the plant.
        occurred_at: the interval the change is estimated to have begun, UTC.
        kind: whether the mean level, the trend, or the variability changed.
        before_value: the mean of the segment before the change.
        after_value: the mean of the segment after it.
        relative_change: signed fractional change from before to after.
        confidence: how strongly the data supports a change here, in ``[0, 1]``.
        segment_length_before: samples in the preceding segment.
        segment_length_after: samples in the following segment. A change detected
            near the end of a series has little data after it and must be treated
            as provisional until more arrives.
    """

    plant_code: str
    occurred_at: datetime
    kind: ChangeKind
    before_value: float
    after_value: float
    relative_change: float
    confidence: float
    segment_length_before: int
    segment_length_after: int


def detect_changepoints(
    series: pl.Series,
    timestamps: pl.Series,
    plant_code: str,
    min_segment_length: int = 168,
    penalty: float | None = None,
) -> list[Changepoint]:
    """Find persistent changes in a consumption series.

    Args:
        series: the consumption series, preferably deseasonalised so that a
            seasonal transition is not mistaken for a structural change.
        timestamps: the timestamps aligned with it.
        plant_code: the plant being analysed.
        min_segment_length: the shortest segment that may be called a regime.
            Defaults to one week of hourly data, below which normal weekly
            variation would be fragmented into spurious changepoints.
        penalty: the complexity penalty; ``None`` selects it from the series
            length and variance rather than using a fixed constant.

    Returns:
        Detected changepoints in chronological order, possibly empty.

    Raises:
        ValueError: if the series is shorter than twice ``min_segment_length``.
    """
    raise NotImplementedError("Phase 5: changepoint detection")


def segment(series: pl.Series, changepoints: list[Changepoint]) -> list[tuple[int, int]]:
    """Split a series into the stable regimes between changepoints.

    Args:
        series: the analysed series.
        changepoints: the detected changepoints.

    Returns:
        Index ranges, one per regime, covering the series without overlap.
    """
    raise NotImplementedError("Phase 5: regime segmentation")


def suggest_baseline_window(changepoints: list[Changepoint], series_length: int) -> tuple[int, int]:
    """Recommend the index range a baseline should be re-fitted over.

    Args:
        changepoints: the detected changepoints.
        series_length: total samples available.

    Returns:
        The index range of the most recent stable regime long enough to fit a
        baseline over.

    Raises:
        ValueError: if no regime since the last changepoint is long enough, which
            means the plant has not been stable long enough to baseline and the
            fact should be reported rather than worked around.
    """
    raise NotImplementedError("Phase 5: baseline window suggestion")


__all__ = ["ChangeKind", "Changepoint", "detect_changepoints", "segment", "suggest_baseline_window"]
