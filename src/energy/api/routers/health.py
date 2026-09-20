"""Liveness and readiness endpoints.

Layer: api

Two endpoints with deliberately different contracts:

``GET /health``
    Liveness. Answers "is this process running?" and touches nothing external,
    so it stays fast and never fails because a dependency is down. Container
    orchestrators restart the process when this fails.

``GET /ready``
    Readiness. Answers "can this process serve traffic?" by probing the
    database, the required extensions, and Silver data freshness. Returns
    ``503`` when unhealthy so a load balancer takes the instance out of
    rotation, and ``200`` when degraded - a database with no data yet is not a
    reason to refuse traffic.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status

from energy.api.deps import SettingsDep
from energy.db.health import HealthStatus, run_health_checks

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="Liveness probe",
    response_description="The process is alive.",
)
async def health(settings: SettingsDep) -> dict[str, Any]:
    """Report that the process is alive.

    Performs no I/O whatsoever: no database query, no object-storage call. A
    failure here means the process itself is wedged.

    Args:
        settings: injected process configuration.

    Returns:
        The application name, version, and environment.
    """
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
    }


@router.get(
    "/ready",
    summary="Readiness probe",
    response_description="Aggregated dependency health.",
    responses={
        status.HTTP_200_OK: {"description": "Ready to serve traffic (healthy or degraded)."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "A required dependency is down."},
    },
)
async def ready(response: Response, settings: SettingsDep) -> dict[str, Any]:
    """Report whether every dependency needed to serve traffic is available.

    Runs the connectivity, extension, and freshness probes from
    :mod:`energy.db.health` against a single shared session.

    Args:
        response: injected so the status code can be set from the probe result.
        settings: injected process configuration.

    Returns:
        The full :class:`~energy.db.health.HealthReport` payload, with each probe
        listed individually so an operator can see which one failed.
    """
    report = run_health_checks()
    if report.status is HealthStatus.UNHEALTHY:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    payload = report.as_dict()
    payload["service"] = settings.app_name
    payload["version"] = settings.app_version
    return payload


__all__ = ["router"]
