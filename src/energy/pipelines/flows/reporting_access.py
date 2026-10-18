"""Give a BI tool such as Power BI its read-only login on the reporting schema.

Layer: pipelines

Responsibility:
    The composition root of the reporting login: it reads the ``REPORTING_*``
    settings and hands them to the PostgreSQL adapter. ``energy reporting
    create-reader`` (and ``make powerbi``) call :func:`create_reader`; the
    ``reporting-bridge`` container of the compose stack then publishes the port
    the tool connects to.

Depends on:
    ``energy.adapters.postgres_reporting``, ``energy.db.session``,
    ``energy.domain``, ``energy.core``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from energy.adapters.postgres_reporting import PostgresReportingAccess
from energy.core.settings import get_settings
from energy.db.session import get_engine
from energy.domain.exceptions import ConfigurationError

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from energy.domain.reporting import ReaderAccess


def create_reader(*, engine: Engine | None = None) -> ReaderAccess:
    """Create the reporting login, or renew its password and settings.

    Running it again is safe: it changes the password to the one configured and
    leaves everything else as it was.

    Args:
        engine: the database engine; the process-wide one when ``None``.

    Returns:
        The login and the views it can read.

    Raises:
        ConfigurationError: if ``REPORTING_DB_PASSWORD`` is not set, the
            database is not migrated, or ``REPORTING_DB_USER`` names a role
            that may do more than read.
        RepositoryError: if the database refuses.
    """
    settings = get_settings()
    reporting = settings.reporting
    password = reporting.db_password.get_secret_value() if reporting.db_password else ""
    if not password:
        raise ConfigurationError(
            "REPORTING_DB_PASSWORD is not set; put the password Power BI will use in .env"
        )
    access = PostgresReportingAccess(engine or get_engine())
    return access.ensure_reader(
        reporting.db_user, password, variation_threshold=settings.equs.variation_threshold
    )


__all__ = ["create_reader"]
