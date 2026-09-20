"""SHAP-based attribution of consumption deviations.

Layer: analytics

Responsibility:
    Answers the question every energy review actually asks: *why* did
    consumption deviate from its baseline? Reports the contribution of each
    driver - production volume, product mix, ambient temperature, operating
    hours - to a given period's deviation, using Shapley values so that the
    contributions sum exactly to the total deviation. That additivity is the
    whole point: a decomposition whose parts do not sum to the whole invites
    endless argument about the remainder, whereas an exact split can be acted on.
    This module explains; it never judges, sets targets, or recommends actions.

Depends on:
    ``energy.analytics.baseline``, ``energy.domain``, ``energy.core``,
    ``polars``, ``shap`` (optional ``analytics`` extra).

Implemented in:
    Phase 5 - Analytics, alerting and reporting
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    import polars as pl

    from energy.analytics.baseline import BaselineModel
    from energy.domain.value_objects import TimeWindow


@dataclass(frozen=True, slots=True)
class DriverContribution:
    """One driver's share of a deviation.

    Attributes:
        driver: the driver name, for example ``"production_units"``.
        contribution_kwh: signed kWh attributable to this driver. Positive means
            it pushed consumption up.
        contribution_pct: this driver's share of the total absolute deviation.
        driver_value: the driver's observed value in the period.
        driver_baseline: its mean over the baseline period, which is what the
            contribution is measured relative to.
    """

    driver: str
    contribution_kwh: float
    contribution_pct: float
    driver_value: float
    driver_baseline: float


@dataclass(frozen=True, slots=True)
class DeviationAttribution:
    """A full explanation of one period's deviation from baseline.

    Attributes:
        plant_code: the plant.
        window: the period explained.
        actual_kwh: measured consumption.
        expected_kwh: what the baseline predicted.
        deviation_kwh: signed difference; positive means overconsumption.
        contributions: per-driver contributions, largest absolute first. These
            sum to ``deviation_kwh`` minus ``unexplained_kwh``.
        unexplained_kwh: the residual no driver accounts for. This is the
            operationally interesting number - it is the part that represents
            genuine efficiency change rather than changed conditions.
        computed_at: when the attribution was produced, UTC.
    """

    plant_code: str
    window: TimeWindow
    actual_kwh: float
    expected_kwh: float
    deviation_kwh: float
    contributions: tuple[DriverContribution, ...]
    unexplained_kwh: float
    computed_at: datetime


def attribute_deviation(
    model: BaselineModel,
    observations: pl.DataFrame,
    window: TimeWindow,
) -> DeviationAttribution:
    """Explain one period's deviation from its baseline.

    Args:
        model: the fitted baseline to measure against.
        observations: the period's actual consumption and driver values.
        window: the period being explained.

    Returns:
        A :class:`DeviationAttribution` whose contributions and
        ``unexplained_kwh`` sum exactly to ``deviation_kwh``.

    Raises:
        ValueError: if a driver the model needs is missing from ``observations``.
        RuntimeError: if the baseline model is not acceptable per
            :attr:`~energy.analytics.baseline.BaselineModel.is_acceptable`.
            Attributing against a poor model produces confident-looking numbers
            with no basis.
    """
    raise NotImplementedError("Phase 5: SHAP deviation attribution")


def rank_drivers(attributions: list[DeviationAttribution], top_n: int = 5) -> pl.DataFrame:
    """Rank drivers by their aggregate contribution across several periods.

    Args:
        attributions: attributions for consecutive periods.
        top_n: how many drivers to return.

    Returns:
        The drivers with the largest mean absolute contribution, which is what
        an improvement programme should target first.
    """
    raise NotImplementedError("Phase 5: driver ranking")


def waterfall_data(attribution: DeviationAttribution) -> pl.DataFrame:
    """Shape an attribution for a waterfall chart.

    Args:
        attribution: the attribution to render.

    Returns:
        Ordered steps from expected to actual consumption: a baseline bar, one
        bar per driver contribution, an unexplained bar, and an actual total.
    """
    raise NotImplementedError("Phase 5: waterfall chart data")


__all__ = [
    "DeviationAttribution",
    "DriverContribution",
    "attribute_deviation",
    "rank_drivers",
    "waterfall_data",
]
