"""Dash page layouts.

Layer: dashboards

Responsibility:
    Defines the static structure of each page - which controls exist, where
    figures are placed, and which component ids callbacks bind to. Layouts are
    pure structure: they contain no data access and no computation, so that a
    page renders instantly and populates through callbacks rather than blocking
    on a database query while the user waits at a blank screen.

Depends on:
    ``energy.dashboards.components``, ``energy.core``, ``dash``.

Implemented in:
    Phase 5 - Analytics, alerting and reporting
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dash import html

# Component ids callbacks bind to. Centralised so a rename cannot silently
# detach a callback - Dash reports an unknown id only at runtime, and only when
# the callback happens to fire.
PLANT_SELECTOR_ID = "plant-selector"
DATE_RANGE_ID = "date-range"
GRANULARITY_ID = "granularity-selector"
KPI_CARDS_ID = "kpi-cards"
CONSUMPTION_CHART_ID = "consumption-chart"
SEC_CHART_ID = "sec-chart"
BASELINE_CHART_ID = "baseline-chart"
COVERAGE_BANNER_ID = "coverage-banner"


def main_layout(plant_codes: Sequence[str]) -> html.Div:
    """Build the application shell containing navigation and the page container.

    Args:
        plant_codes: plants the current user may select.

    Returns:
        The root layout.
    """
    raise NotImplementedError("Phase 5: application shell layout")


def overview_page(plant_codes: Sequence[str]) -> html.Div:
    """Build the landing page: headline KPI cards and consumption trend.

    Reads ``gold.plant_daily_summary`` rather than pivoting the long KPI table,
    so the page is a single indexed lookup.

    Args:
        plant_codes: plants available in the selector.

    Returns:
        The overview page layout.
    """
    raise NotImplementedError("Phase 5: overview page layout")


def efficiency_page(plant_codes: Sequence[str]) -> html.Div:
    """Build the efficiency page: SEC trend, baseline comparison, attribution.

    Args:
        plant_codes: plants available in the selector.

    Returns:
        The efficiency page layout.
    """
    raise NotImplementedError("Phase 5: efficiency page layout")


def data_quality_page(plant_codes: Sequence[str]) -> html.Div:
    """Build the data-quality page: coverage, quarantine, source freshness.

    Deliberately given equal prominence to the KPI pages. Users trust numbers
    more when they can see how complete the data behind them is, and a hidden
    quality page is one nobody checks until a figure is already disputed.

    Args:
        plant_codes: plants available in the selector.

    Returns:
        The data-quality page layout.
    """
    raise NotImplementedError("Phase 5: data quality page layout")


__all__ = [
    "BASELINE_CHART_ID",
    "CONSUMPTION_CHART_ID",
    "COVERAGE_BANNER_ID",
    "DATE_RANGE_ID",
    "GRANULARITY_ID",
    "KPI_CARDS_ID",
    "PLANT_SELECTOR_ID",
    "SEC_CHART_ID",
    "data_quality_page",
    "efficiency_page",
    "main_layout",
    "overview_page",
]
