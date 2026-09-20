"""Dependency injection wiring for the API layer.

Layer: api

Declares the ``Annotated`` aliases FastAPI route handlers use to receive their
collaborators. This is the single place where concrete adapters are bound to the
abstract ports the routers depend on, which is what keeps the routers testable:
a test overrides the provider, not the router.

Phase 1 wires settings, database sessions, and the request id. Repository and
service providers are declared here with their final signatures but return
placeholder implementations until Phase 2.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from energy.core.settings import Settings, get_settings
from energy.db.session import get_sessionmaker

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def get_app_settings() -> Settings:
    """Return the process-wide settings.

    Returns:
        The cached :class:`~energy.core.settings.Settings` instance. Override
        this dependency in tests to inject a different configuration.
    """
    return get_settings()


def get_db_session() -> Iterator[Session]:
    """Provide one database session per request.

    The session is committed when the handler returns normally and rolled back if
    it raises, then always closed. Handlers receive an open session and must not
    commit or close it themselves.

    Yields:
        An open :class:`~sqlalchemy.orm.Session`.

    Raises:
        Exception: whatever the handler raised, after rolling back.
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_request_id(request: Request) -> str:
    """Return the correlation id bound to this request.

    Args:
        request: the inbound request, populated by
            :class:`~energy.api.middleware.RequestContextMiddleware`.

    Returns:
        The ``X-Request-ID`` value, or an empty string if the middleware did not
        run (which only happens in unit tests that bypass it).
    """
    return str(getattr(request.state, "request_id", ""))


def require_api_key(
    settings: Annotated[Settings, Depends(get_app_settings)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> str:
    """Reject requests that do not present a valid API key.

    Phase 1 accepts any non-empty key outside production so that the scaffold is
    usable locally; Phase 3 replaces this with real key lookup and role checks in
    :mod:`energy.api.security`.

    Args:
        settings: process configuration, used to decide how strict to be.
        x_api_key: the inbound ``X-API-Key`` header.

    Returns:
        The presented key.

    Raises:
        HTTPException: ``401`` when the header is missing in a deployed
            environment.
    """
    if not x_api_key:
        if settings.is_production:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing API key",
                headers={"WWW-Authenticate": settings.api.api_key_header},
            )
        return ""
    return x_api_key


# -----------------------------------------------------------------------------
# Annotated aliases - route handlers depend on these, never on the functions.
# -----------------------------------------------------------------------------
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SessionDep = Annotated["Session", Depends(get_db_session)]
RequestIdDep = Annotated[str, Depends(get_request_id)]
APIKeyDep = Annotated[str, Depends(require_api_key)]

__all__ = [
    "APIKeyDep",
    "RequestIdDep",
    "SessionDep",
    "SettingsDep",
    "get_app_settings",
    "get_db_session",
    "get_request_id",
    "require_api_key",
]
