"""Anomaly detection over consumption series.

Layer: analytics

Responsibility:
    Flags consumption that does not fit its own recent pattern, operating on the
    residual from :mod:`energy.analytics.decomposition` rather than the raw
    series so that expected cycles are not reported as anomalies. The module's
    hardest requirement is not sensitivity but precision: an alerting system that
    cries wolf is switched off within a fortnight, after which it detects
    nothing. Every detector therefore reports a severity and an explanation, and
    thresholds are expressed in interpretable units (multiples of the series'
    own variability) rather than as opaque tuned constants.

Depends on:
    ``energy.analytics.decomposition``, ``energy.domain``, ``energy.core``,
    ``polars``, ``scikit-learn`` (optional ``analytics`` extra).

Implemented in:
    Phase 5 - Analytics, alerting and reporting
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import datetime

    import polars as pl

#: Which detector produced a finding.
DetectorKind = Literal["residual_zscore", "isolation_forest", "level_shift", "flatline"]


@dataclass(frozen=True, slots=True)
class Anomaly:
    """One detected anomaly.

    Attributes:
        plant_code: the plant.
        meter_id: the meter, or ``None`` for plant-level findings.
        detected_at: the interval the anomaly occurred in, UTC.
        observed: the measured value.
        expected: what the pattern implied.
        deviation_sigma: how many standard deviations of the series' own
            residual the observation sits from expectation. Reporting in sigma
            rather than percent is what makes a threshold meaningful across
            plants of very different sizes.
        detector: which detector found it.
        severity: ``low``, ``medium``, or ``high``.
        explanation: a human-readable account of why this was flagged, in
            English. Mandatory: an alert a plant engineer cannot interpret will
            be ignored.
    """

    plant_code: str
    meter_id: str | None
    detected_at: datetime
    observed: float
    expected: float
    deviation_sigma: float
    detector: DetectorKind
    severity: Literal["low", "medium", "high"]
    explanation: str


def detect_residual_outliers(
    residual: pl.Series,
    timestamps: pl.Series,
    plant_code: str,
    sigma_threshold: float = 3.0,
) -> list[Anomaly]:
    """Flag residuals that are implausibly far from zero.

    Args:
        residual: the residual component from decomposition.
        timestamps: the timestamps aligned with ``residual``.
        plant_code: the plant being analysed.
        sigma_threshold: how many robust standard deviations count as anomalous.
            Uses median absolute deviation rather than the standard deviation, so
            that the outliers being detected do not inflate the threshold meant
            to catch them.

    Returns:
        One :class:`Anomaly` per flagged interval, in chronological order.

    Raises:
        ValueError: if ``residual`` and ``timestamps`` differ in length.
    """
    raise NotImplementedError("Phase 5: residual outlier detection")


def detect_flatlines(
    series: pl.Series,
    timestamps: pl.Series,
    plant_code: str,
    min_run_length: int = 6,
) -> list[Anomaly]:
    """Flag runs of identical values, which usually mean a stuck instrument.

    A frozen sensor reports a plausible constant rather than an obvious error, so
    it passes every range check and can go unnoticed for weeks. Detecting it
    requires looking for the absence of variation, not for extreme values.

    Args:
        series: the raw consumption series.
        timestamps: the timestamps aligned with it.
        plant_code: the plant being analysed.
        min_run_length: how many consecutive identical readings constitute a
            flatline.

    Returns:
        One anomaly per detected run, covering its full extent.
    """
    raise NotImplementedError("Phase 5: flatline detection")


def detect_multivariate(
    features: pl.DataFrame,
    plant_code: str,
    contamination: float = 0.01,
) -> list[Anomaly]:
    """Flag observations that are unusual in combination rather than individually.

    Catches the cases univariate detection misses: normal energy at normal
    production, but in a combination the plant has never operated at.

    Args:
        features: one row per period with the driver values and consumption.
        plant_code: the plant being analysed.
        contamination: expected share of anomalous observations, which sets the
            decision boundary.

    Returns:
        One anomaly per flagged observation, with the explanation naming the
        features that contributed most.
    """
    raise NotImplementedError("Phase 5: multivariate anomaly detection")


__all__ = [
    "Anomaly",
    "DetectorKind",
    "detect_flatlines",
    "detect_multivariate",
    "detect_residual_outliers",
]
