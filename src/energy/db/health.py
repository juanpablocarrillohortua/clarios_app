"""Database connectivity, extension, and data-freshness probes.

Layer: db (infrastructure)

Backs the ``/ready`` endpoint and the ops runbook. Three independent questions
are answered separately so an operator can tell *which* thing is wrong:

1. Can we open a connection and run a query at all?
2. Is the TimescaleDB extension installed and are the hypertables registered?
3. How stale is the newest Silver row?

Every probe is defensive: a missing table or a refused connection is reported as
an unhealthy result, never raised, because a health endpoint that throws is
useless to a load balancer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from energy.core.constants import SILVER_SCHEMA
from energy.core.logging import get_logger
from energy.core.settings import get_settings
from energy.db.session import session_scope

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = get_logger(__name__)

REQUIRED_EXTENSIONS: tuple[str, ...] = ("timescaledb", "pgcrypto")


class HealthStatus(StrEnum):
    """Overall outcome of a health probe."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of a single probe.

    Attributes:
        name: probe identifier, for example ``"connectivity"``.
        status: healthy, degraded, or unhealthy.
        detail: short human-readable explanation, always in English.
        data: structured extras, for example the measured lag in hours.
    """

    name: str
    status: HealthStatus
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Return ``True`` when this probe did not fail outright."""
        return self.status is not HealthStatus.UNHEALTHY

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "name": self.name,
            "status": self.status.value,
            "detail": self.detail,
            "data": self.data,
        }


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Aggregate of every probe, with the worst status winning.

    Attributes:
        status: the aggregate status.
        checks: individual probe results, in the order they ran.
        checked_at: when the report was produced, timezone-aware UTC.
    """

    status: HealthStatus
    checks: tuple[CheckResult, ...]
    checked_at: datetime

    @property
    def ok(self) -> bool:
        """Return ``True`` when no probe reported ``UNHEALTHY``."""
        return self.status is not HealthStatus.UNHEALTHY

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation for the ``/ready`` payload."""
        return {
            "status": self.status.value,
            "checked_at": self.checked_at.isoformat(),
            "checks": [check.as_dict() for check in self.checks],
        }


def check_connectivity(session: Session | None = None) -> CheckResult:
    """Confirm the database accepts connections and answers a trivial query.

    Args:
        session: an existing session to reuse; a short-lived one is opened when
            omitted.

    Returns:
        ``HEALTHY`` when ``SELECT 1`` returns, ``UNHEALTHY`` with the driver
        error message otherwise.
    """
    try:
        if session is not None:
            session.execute(text("SELECT 1"))
        else:
            with session_scope() as scoped:
                scoped.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("database connectivity probe failed", error=str(exc))
        return CheckResult(
            name="connectivity",
            status=HealthStatus.UNHEALTHY,
            detail="Database is not reachable",
            data={"error": type(exc).__name__},
        )
    return CheckResult(
        name="connectivity",
        status=HealthStatus.HEALTHY,
        detail="Database responded to SELECT 1",
    )


def check_extensions(session: Session | None = None) -> CheckResult:
    """Confirm every required PostgreSQL extension is installed.

    TimescaleDB is what turns the Bronze and Silver tables into hypertables; a
    database missing it will accept writes but silently lose every partitioning,
    compression, and retention guarantee the platform depends on.

    Args:
        session: an existing session to reuse; a short-lived one is opened when
            omitted.

    Returns:
        ``HEALTHY`` when all of :data:`REQUIRED_EXTENSIONS` are present,
        ``UNHEALTHY`` listing the missing ones otherwise.
    """
    query = text("SELECT extname, extversion FROM pg_extension")
    try:
        if session is not None:
            rows = session.execute(query).all()
        else:
            with session_scope() as scoped:
                rows = scoped.execute(query).all()
    except SQLAlchemyError as exc:
        return CheckResult(
            name="extensions",
            status=HealthStatus.UNHEALTHY,
            detail="Could not read pg_extension",
            data={"error": type(exc).__name__},
        )

    installed: dict[str, str] = {str(row[0]): str(row[1]) for row in rows}
    missing = [name for name in REQUIRED_EXTENSIONS if name not in installed]
    if missing:
        return CheckResult(
            name="extensions",
            status=HealthStatus.UNHEALTHY,
            detail=f"Missing required extensions: {', '.join(missing)}",
            data={"missing": missing, "installed": sorted(installed)},
        )
    return CheckResult(
        name="extensions",
        status=HealthStatus.HEALTHY,
        detail="All required extensions are installed",
        data={name: installed[name] for name in REQUIRED_EXTENSIONS},
    )


def check_freshness(session: Session | None = None) -> CheckResult:
    """Measure how stale the newest Silver reading is.

    Args:
        session: an existing session to reuse; a short-lived one is opened when
            omitted.

    Returns:
        ``HEALTHY`` when the newest ``silver.readings`` row is within
        ``DATA_FRESHNESS_MAX_LAG_HOURS``, ``DEGRADED`` when it is older or the
        table is still empty, and ``DEGRADED`` (never ``UNHEALTHY``) when the
        table does not exist yet - a freshly migrated database is not broken,
        it is simply unloaded.
    """
    settings = get_settings()
    max_lag = settings.data.freshness_max_lag_hours
    # SILVER_SCHEMA is a module-level constant, never caller-supplied, so this
    # interpolation cannot carry untrusted input.
    query = text(f"SELECT max(ts_utc) FROM {SILVER_SCHEMA}.readings")  # noqa: S608  # nosec B608

    try:
        if session is not None:
            newest = session.execute(query).scalar_one_or_none()
        else:
            with session_scope() as scoped:
                newest = scoped.execute(query).scalar_one_or_none()
    except SQLAlchemyError as exc:
        logger.info("silver freshness probe could not read the table", error=str(exc))
        return CheckResult(
            name="freshness",
            status=HealthStatus.DEGRADED,
            detail="Silver readings table is not queryable yet",
            data={"error": type(exc).__name__, "max_lag_hours": max_lag},
        )

    if newest is None:
        return CheckResult(
            name="freshness",
            status=HealthStatus.DEGRADED,
            detail="Silver layer contains no readings yet",
            data={"max_lag_hours": max_lag},
        )

    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=UTC)
    lag_hours = (datetime.now(UTC) - newest).total_seconds() / 3600

    status = HealthStatus.HEALTHY if lag_hours <= max_lag else HealthStatus.DEGRADED
    return CheckResult(
        name="freshness",
        status=status,
        detail=f"Newest Silver reading is {lag_hours:.1f}h old (threshold {max_lag}h)",
        data={
            "newest_reading_utc": newest.isoformat(),
            "lag_hours": round(lag_hours, 2),
            "max_lag_hours": max_lag,
        },
    )


def run_health_checks(*, include_freshness: bool = True) -> HealthReport:
    """Run every probe against a single shared session and aggregate the result.

    Args:
        include_freshness: skip the freshness probe when ``False``. Useful for a
            fast readiness check immediately after deployment, before any
            pipeline has run.

    Returns:
        A :class:`HealthReport` whose status is the worst individual status:
        any ``UNHEALTHY`` makes the report unhealthy, otherwise any ``DEGRADED``
        makes it degraded.
    """
    checks: list[CheckResult] = []
    try:
        with session_scope() as session:
            connectivity = check_connectivity(session)
            checks.append(connectivity)
            if connectivity.ok:
                checks.append(check_extensions(session))
                if include_freshness:
                    checks.append(check_freshness(session))
    except SQLAlchemyError as exc:
        checks.append(
            CheckResult(
                name="connectivity",
                status=HealthStatus.UNHEALTHY,
                detail="Database is not reachable",
                data={"error": type(exc).__name__},
            )
        )

    if any(check.status is HealthStatus.UNHEALTHY for check in checks):
        overall = HealthStatus.UNHEALTHY
    elif any(check.status is HealthStatus.DEGRADED for check in checks):
        overall = HealthStatus.DEGRADED
    else:
        overall = HealthStatus.HEALTHY

    return HealthReport(status=overall, checks=tuple(checks), checked_at=datetime.now(UTC))


__all__ = [
    "REQUIRED_EXTENSIONS",
    "CheckResult",
    "HealthReport",
    "HealthStatus",
    "check_connectivity",
    "check_extensions",
    "check_freshness",
    "run_health_checks",
]
