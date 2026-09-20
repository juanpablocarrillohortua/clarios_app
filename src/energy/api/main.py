"""FastAPI application factory.

Layer: api

Builds the ASGI application: configures logging, installs middleware, registers
routers, and maps domain exceptions onto HTTP responses. The factory pattern
(rather than a module-level ``app``) is what lets tests build an isolated
instance with overridden settings.

This module may import ``domain``, ``services``, ``db`` and ``core``. It must
never import ``pipelines`` or ``dashboards``: those are sibling entry points,
not dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from energy.api.middleware import RequestContextMiddleware
from energy.api.routers import health, ingest, kpis, runs, uploads
from energy.core.logging import configure_from_settings, get_logger
from energy.core.settings import Settings, get_settings
from energy.db.session import dispose_engine
from energy.domain.exceptions import EnergyError, EntityNotFoundError

logger = get_logger(__name__)

DESCRIPTION = """
Industrial energy and gas consumption monitoring across multiple plants.

The API exposes the **Gold** layer (KPIs and aggregates) for dashboards and
reporting, and accepts new data into the **Bronze** layer through the upload and
ingest endpoints. It never exposes raw Bronze rows directly.
"""

TAGS_METADATA = [
    {"name": "health", "description": "Liveness and readiness probes."},
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

    app.include_router(health.router)
    app.include_router(kpis.router)
    app.include_router(uploads.router)
    app.include_router(ingest.router)
    app.include_router(runs.router)

    _register_exception_handlers(app)

    return app


app = create_app()

__all__ = ["app", "create_app", "lifespan"]
