"""Dash callback registration.

Layer: dashboards

Responsibility:
    Wires user interaction to data. Each callback reads the control values,
    calls a service, and returns a figure or a component - it never queries the
    database directly and never computes a KPI, so that what the dashboard shows
    is exactly what Gold holds. Callbacks are registered against a service
    instance rather than importing one, which is what makes them testable
    without a database.

Depends on:
    ``energy.dashboards.layouts``, ``energy.dashboards.components``,
    ``energy.services.kpi_service``, ``energy.core``, ``dash``.

Implemented in:
    Phase 5 - Analytics, alerting and reporting
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dash import Dash

    from energy.services.kpi_service import KPIService


def register_callbacks(app: Dash, kpi_service: KPIService) -> None:
    """Attach every callback to the application.

    Args:
        app: the Dash application to register against.
        kpi_service: the service callbacks read data through.
    """
    raise NotImplementedError("Phase 5: callback registration")


def register_overview_callbacks(app: Dash, kpi_service: KPIService) -> None:
    """Attach the overview page's callbacks.

    Args:
        app: the Dash application.
        kpi_service: the service to read through.
    """
    raise NotImplementedError("Phase 5: overview callbacks")


def register_efficiency_callbacks(app: Dash, kpi_service: KPIService) -> None:
    """Attach the efficiency page's callbacks.

    Args:
        app: the Dash application.
        kpi_service: the service to read through.
    """
    raise NotImplementedError("Phase 5: efficiency callbacks")


def register_quality_callbacks(app: Dash, kpi_service: KPIService) -> None:
    """Attach the data-quality page's callbacks.

    Args:
        app: the Dash application.
        kpi_service: the service to read through.
    """
    raise NotImplementedError("Phase 5: data quality callbacks")


__all__ = [
    "register_callbacks",
    "register_efficiency_callbacks",
    "register_overview_callbacks",
    "register_quality_callbacks",
]
