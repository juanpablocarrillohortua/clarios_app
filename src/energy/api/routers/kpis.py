"""Gold-layer KPI query endpoints.

Layer: api

Responsibility:
    Exposes the computed KPIs that dashboards, reports and external consumers
    read. Every handler resolves to rows already stored in Gold by
    :mod:`energy.pipelines.tasks.compute_kpis`; nothing here recomputes a KPI on
    the fly, so a number served by the API and the same number drawn on a chart
    can never diverge. Handlers delegate to
    :class:`~energy.services.kpi_service.KPIService` and perform no arithmetic of
    their own.

Depends on:
    ``energy.api.deps``, ``energy.api.schemas``, ``energy.services.kpi_service``,
    ``energy.domain``.

Implemented in:
    Phase 4 - KPI computation and Gold layer
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, status

from energy.api.deps import SessionDep, SettingsDep
from energy.api.schemas.responses import KPISeriesResponse, KPISummaryResponse

router = APIRouter(prefix="/kpis", tags=["kpis"])


@router.get("", summary="Query a KPI time series", status_code=status.HTTP_200_OK)
async def list_kpis(
    session: SessionDep,
    settings: SettingsDep,
    plant_code: str = Query(description="Plant code to query, for example MTY-01."),
    kpi_key: str = Query(description="KPI key as defined in config/kpis.yaml."),
    start: datetime = Query(description="Period start, inclusive, ISO-8601 with UTC offset."),
    end: datetime = Query(description="Period end, exclusive, ISO-8601 with UTC offset."),
    granularity: str = Query(default="day", description="hour, day, week, or month."),
) -> KPISeriesResponse:
    """Return one KPI as a time series for one plant.

    Args:
        session: injected database session.
        settings: injected process configuration.
        plant_code: which plant to read.
        kpi_key: which KPI to read; must exist in ``config/kpis.yaml``.
        start: period start, inclusive. Must be timezone-aware.
        end: period end, exclusive. Must be timezone-aware and after ``start``.
        granularity: the aggregation level to read.

    Returns:
        A :class:`~energy.api.schemas.responses.KPISeriesResponse` carrying one
        point per period, each with its coverage fraction so the consumer can
        tell a complete period from a partially observed one.

    Raises:
        UnknownKPIError: if ``kpi_key`` is not configured, surfaced as HTTP 400.
        EntityNotFoundError: if ``plant_code`` is unknown, surfaced as HTTP 404.
        InvalidTimeWindowError: if the window is inverted, surfaced as HTTP 400.
    """
    raise NotImplementedError("Phase 4: KPI time series query")


@router.get("/summary", summary="Latest KPI values across plants", status_code=status.HTTP_200_OK)
async def kpi_summary(
    session: SessionDep,
    settings: SettingsDep,
    plant_codes: list[str] = Query(default=[], description="Plants to include; empty means all."),
) -> KPISummaryResponse:
    """Return the most recent value of every KPI for the requested plants.

    Backs the dashboard landing page, which needs one number per KPI per plant
    rather than a full series.

    Args:
        session: injected database session.
        settings: injected process configuration.
        plant_codes: plants to include; empty includes every plant.

    Returns:
        A :class:`~energy.api.schemas.responses.KPISummaryResponse` with the
        latest stored value per plant and KPI, and the timestamp it applies to.

    Raises:
        EntityNotFoundError: if a requested plant code is unknown.
    """
    raise NotImplementedError("Phase 4: cross-plant KPI summary")


@router.get(
    "/{plant_code}/coverage",
    summary="Data coverage behind a period's KPIs",
    status_code=status.HTTP_200_OK,
)
async def kpi_coverage(
    session: SessionDep,
    plant_code: str,
    start: datetime = Query(description="Period start, inclusive."),
    end: datetime = Query(description="Period end, exclusive."),
) -> dict[str, object]:
    """Report how much of a period was backed by reliable readings.

    Args:
        session: injected database session.
        plant_code: the plant to report on.
        start: period start, inclusive.
        end: period end, exclusive.

    Returns:
        Per-KPI coverage fractions plus the count of quarantined source rows, so
        a report can footnote which figures rest on incomplete data.

    Raises:
        EntityNotFoundError: if the plant code is unknown.
    """
    raise NotImplementedError("Phase 4: coverage reporting endpoint")


__all__ = ["router"]
