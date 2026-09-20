"""Time series decomposition for cyclical industrial processes.

Layer: analytics

Responsibility:
    Separates a consumption series into trend, seasonal and residual components
    so that the three can be reasoned about independently. Industrial energy has
    several superimposed cycles - shift patterns within a day, weekday against
    weekend, annual weather - and a raw series conflates them. Decomposition is
    what lets the platform say "consumption rose 8%, of which 6 points are the
    expected seasonal increase", and it is the preprocessing step that makes
    anomaly detection tractable: anomalies are found in the residual, not in the
    raw signal, where every Monday morning would otherwise look like a spike.

Depends on:
    ``energy.domain``, ``energy.core``, ``polars``, ``statsmodels`` (optional
    ``analytics`` extra).

Implemented in:
    Phase 5 - Analytics, alerting and reporting
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import polars as pl

#: Whether components combine by addition or multiplication.
DecompositionModel = Literal["additive", "multiplicative"]


@dataclass(frozen=True, slots=True)
class Decomposition:
    """A series split into its components.

    Attributes:
        trend: the slow-moving level, with cycles removed.
        seasonal: the repeating cyclical component.
        residual: what neither explains. This is the signal anomaly detection
            operates on.
        model: whether components combine additively or multiplicatively.
        period: the seasonal period in samples, for example 24 for a daily cycle
            on hourly data.
        strength_seasonal: how much of the variance the seasonal component
            explains, in ``[0, 1]``. A low value means the series has no reliable
            cycle and seasonal adjustment will add noise rather than remove it.
        strength_trend: how much the trend explains, in ``[0, 1]``.
    """

    trend: pl.Series
    seasonal: pl.Series
    residual: pl.Series
    model: DecompositionModel
    period: int
    strength_seasonal: float
    strength_trend: float


def decompose_stl(
    series: pl.Series,
    period: int,
    robust: bool = True,
) -> Decomposition:
    """Decompose a series using STL.

    STL is preferred over classical decomposition because it tolerates the gaps
    and outliers real plant data contains, and allows the seasonal shape itself
    to evolve - which it does, as shift patterns change.

    Args:
        series: the consumption series, on a regular grid with no gaps. Missing
            intervals must be imputed or the series split beforehand.
        period: the seasonal period in samples.
        robust: downweight outliers during fitting. Leave enabled for plant data,
            where a single instrument fault otherwise distorts the whole
            seasonal estimate.

    Returns:
        The :class:`Decomposition`.

    Raises:
        ValueError: if the series is shorter than two full periods, or contains
            nulls.
    """
    raise NotImplementedError("Phase 5: STL decomposition")


def detect_period(series: pl.Series, candidates: tuple[int, ...] = (24, 168, 8760)) -> int:
    """Identify the dominant seasonal period of a series.

    Args:
        series: the consumption series.
        candidates: periods to test, defaulting to daily, weekly and annual
            cycles on hourly data.

    Returns:
        The candidate period with the strongest autocorrelation.

    Raises:
        ValueError: if the series is too short to test the smallest candidate.
    """
    raise NotImplementedError("Phase 5: seasonal period detection")


def deseasonalise(series: pl.Series, decomposition: Decomposition) -> pl.Series:
    """Remove the seasonal component from a series.

    Args:
        series: the original series.
        decomposition: its decomposition.

    Returns:
        The series with the seasonal component removed, suitable for
        trend comparison across periods that would otherwise not be comparable.
    """
    raise NotImplementedError("Phase 5: seasonal adjustment")


__all__ = [
    "Decomposition",
    "DecompositionModel",
    "decompose_stl",
    "deseasonalise",
    "detect_period",
]
