"""Reusable Plotly figure builders.

Layer: dashboards

Responsibility:
    Turns a dataframe into a figure. Every function here is pure: it takes data
    as an argument, never fetches it, and returns a figure without side effects.
    The convention these builders enforce is that incomplete data is always
    visible - periods with low coverage are rendered differently rather than
    drawn identically to complete ones, because a chart that hides its own gaps
    invites confident conclusions from thin data.

Depends on:
    ``energy.core``, ``plotly``, ``polars``.

Implemented in:
    Phase 5 - Analytics, alerting and reporting
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import polars as pl
    from plotly.graph_objects import Figure

#: Coverage below which a data point is rendered as provisional.
LOW_COVERAGE_THRESHOLD = 0.8


def consumption_trend(frame: pl.DataFrame, title: str) -> Figure:
    """Build a consumption time-series chart.

    Args:
        frame: one row per period with ``period_start``, ``value`` and
            ``coverage``.
        title: chart title, in English.

    Returns:
        A line chart with low-coverage periods drawn with a distinct marker, so
        a dip caused by missing data is not read as a real reduction.
    """
    raise NotImplementedError("Phase 5: consumption trend chart")


def kpi_card(
    label: str, value: float, unit: str, previous: float | None, coverage: float
) -> object:
    """Build a headline KPI card with a period-over-period delta.

    Args:
        label: the KPI's display name.
        value: the current value.
        unit: the unit to display.
        previous: the preceding period's value; ``None`` hides the delta.
        coverage: the current period's coverage, shown when below
            :data:`LOW_COVERAGE_THRESHOLD`.

    Returns:
        A Dash component rendering the card.
    """
    raise NotImplementedError("Phase 5: KPI card component")


def baseline_comparison(frame: pl.DataFrame, title: str) -> Figure:
    """Build an actual-against-baseline chart with its prediction interval.

    Args:
        frame: one row per period with ``period_start``, ``actual_kwh``,
            ``expected_kwh`` and the interval bounds.
        title: chart title.

    Returns:
        A chart with the actual series over a shaded prediction interval, so a
        deviation is judged against the model's uncertainty rather than read as
        exact.
    """
    raise NotImplementedError("Phase 5: baseline comparison chart")


def attribution_waterfall(frame: pl.DataFrame, title: str) -> Figure:
    """Build a waterfall chart explaining a deviation.

    Args:
        frame: the ordered steps from
            :func:`energy.analytics.attribution.waterfall_data`.
        title: chart title.

    Returns:
        A waterfall from expected to actual consumption, one step per driver.
    """
    raise NotImplementedError("Phase 5: attribution waterfall chart")


def coverage_heatmap(frame: pl.DataFrame, title: str) -> Figure:
    """Build a coverage heatmap over meters and periods.

    Args:
        frame: one row per meter and period with its coverage fraction.
        title: chart title.

    Returns:
        A heatmap making systematic gaps - one meter always missing, or every
        meter missing the same night - visible at a glance.
    """
    raise NotImplementedError("Phase 5: coverage heatmap")


__all__ = [
    "LOW_COVERAGE_THRESHOLD",
    "attribution_waterfall",
    "baseline_comparison",
    "consumption_trend",
    "coverage_heatmap",
    "kpi_card",
]
