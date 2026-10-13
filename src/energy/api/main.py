"""FastAPI application factory.

Layer: api

Builds the ASGI application: configures logging, installs middleware, registers
routers, and maps domain exceptions onto HTTP responses. The factory pattern
(rather than a module-level ``app``) is what lets tests build an isolated
instance with overridden settings.

This module may import ``domain``, ``services``, ``db`` and ``core``. It must
never import ``pipelines`` or ``dashboards``: those are sibling entry points,
not dependencies.

Who may call what is decided here, where the routers are mounted, so the whole
policy can be read in one place:

- ``/health``, ``/ready``, ``/config`` and the API's own documentation are
  public (:data:`PUBLIC_PATHS`);
- ``/me`` needs a signed-in person;
- every other router is mounted with the permission it needs;
- ``/ingest`` is for devices and keeps its API key (:data:`DEVICE_PATH_PREFIX`).

A router mounted without one of these fails
``tests/unit/test_api_auth.py``, so a new endpoint cannot be left open by
oversight.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from energy.api.deps import requires
from energy.api.middleware import RequestContextMiddleware
from energy.api.routers import client_config, health, ingest, kpis, me, runs, uploads
from energy.api.security import Permission
from energy.core.logging import configure_from_settings, get_logger
from energy.core.settings import Settings, get_settings
from energy.db.session import dispose_engine
from energy.domain.exceptions import (
    AuthenticationError,
    EnergyError,
    EntityNotFoundError,
    IdentityProviderUnavailableError,
    PermissionDeniedError,
    SignInNotConfiguredError,
)

logger = get_logger(__name__)

#: Paths anyone may call, signed in or not.
PUBLIC_PATHS: Final = frozenset(
    {"/health", "/ready", "/config", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
)
#: Paths for devices, which present an API key and not a person's token.
DEVICE_PATH_PREFIX: Final = "/ingest"

DESCRIPTION = """
Industrial energy and gas consumption monitoring across multiple plants.

The API exposes the **Gold** layer (KPIs and aggregates) for dashboards and
reporting, and accepts new data into the **Bronze** layer through the upload and
ingest endpoints. It never exposes raw Bronze rows directly.
"""

TAGS_METADATA = [
    {"name": "health", "description": "Liveness and readiness probes."},
    {"name": "session", "description": "Sign-in settings, and who is signed in."},
    {"name": "kpis", "description": "Gold-layer KPI queries for dashboards and reports."},
    {"name": "uploads", "description": "Excel and CSV file ingestion into Bronze."},
    {"name": "ingest", "description": "Sensor and historian push endpoint into Bronze."},
    {"name": "runs", "description": "Pipeline run status and history."},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup and shutdown of process-wide resources.

    Args:
        app: the application being started. Used only to read its state.

    Yields:
        Control back to the server while the application is running.
    """
    settings: Settings = app.state.settings
    logger.info(
        "api starting",
        app_name=settings.app_name,
        environment=settings.app_env,
        version=settings.app_version,
    )
    yield
    dispose_engine()
    logger.info("api stopped")


def _register_exception_handlers(app: FastAPI) -> None:
    """Map domain exceptions onto HTTP responses.

    ``NotImplementedError`` becomes ``501 Not Implemented`` rather than a 500.
    Phase 1 ships placeholder routers whose handlers raise it deliberately, so a
    call to an unbuilt endpoint must read as "not built yet", not "server bug".

    A caller who cannot be let in gets 401 (no valid token), 403 (no
    permission) or 503 (the token could not be checked, which is this side's
    fault). The 401 says nothing about why: the reason is logged.

    Args:
        app: the application to attach handlers to.
    """

    @app.exception_handler(NotImplementedError)
    async def _not_implemented(_request: Request, exc: NotImplementedError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            content={
                "code": "not_implemented",
                "message": str(exc) or "This endpoint is not implemented yet",
                "details": {},
            },
        )

    @app.exception_handler(EntityNotFoundError)
    async def _not_found(_request: Request, exc: EntityNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=exc.as_dict())

    @app.exception_handler(AuthenticationError)
    async def _unauthenticated(_request: Request, exc: AuthenticationError) -> JSONResponse:
        # The caller learns only that it was refused. Why goes to the log: a
        # detailed answer would tell an attacker which check to work on.
        reason = str(exc.details.get("reason", "unknown"))
        presented = reason != "missing"
        # A request without a token is routine. A token that was refused is
        # worth a warning, so it survives a quieter log level.
        log = logger.warning if presented else logger.info
        log("caller not authenticated", reason=reason)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "code": exc.code,
                "message": (
                    "The access token was refused" if presented else "Sign in to call this endpoint"
                ),
                "details": {},
            },
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"' if presented else "Bearer"},
        )

    @app.exception_handler(PermissionDeniedError)
    async def _forbidden(_request: Request, exc: PermissionDeniedError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=exc.as_dict())

    @app.exception_handler(SignInNotConfiguredError)
    @app.exception_handler(IdentityProviderUnavailableError)
    async def _cannot_check_tokens(_request: Request, exc: EnergyError) -> JSONResponse:
        logger.error("a token could not be checked", code=exc.code, detail=str(exc))
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=exc.as_dict())

    @app.exception_handler(EnergyError)
    async def _domain_error(_request: Request, exc: EnergyError) -> JSONResponse:
        logger.warning("domain error surfaced to the API", code=exc.code, detail=str(exc))
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=exc.as_dict())


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application.

    Args:
        settings: configuration to use. Defaults to the cached process settings;
            tests pass an explicit instance to isolate themselves from ``.env``.

    Returns:
        A fully wired :class:`~fastapi.FastAPI` instance with logging configured,
        middleware installed, routers mounted, and exception handlers registered.
    """
    settings = settings or get_settings()
    configure_from_settings()

    app = FastAPI(
        title="Clarios Energy API",
        description=DESCRIPTION,
        version=settings.app_version,
        openapi_tags=TAGS_METADATA,
        root_path=settings.api.root_path,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Public.
    app.include_router(health.router)
    app.include_router(client_config.router)
    # Any signed-in person: the handler itself asks for the caller.
    app.include_router(me.router)
    # People, by permission.
    may_view = [Depends(requires(Permission.DASHBOARD_VIEW))]
    may_load = [Depends(requires(Permission.LOADS_WRITE))]
    app.include_router(kpis.router, dependencies=may_view)
    app.include_router(uploads.router, dependencies=may_load)
    app.include_router(runs.router, dependencies=may_load)
    # Devices, by API key (Phase 3).
    app.include_router(ingest.router)

    _register_exception_handlers(app)

    return app


app = create_app()

__all__ = ["DEVICE_PATH_PREFIX", "PUBLIC_PATHS", "app", "create_app", "lifespan"]
