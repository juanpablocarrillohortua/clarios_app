"""Shared pytest fixtures.

Database strategy, in priority order:

1. ``DATABASE_URL`` in the environment, if it points at a reachable database.
   This is the CI path, where a TimescaleDB service container is already running.
2. A ``testcontainers``-managed TimescaleDB container. This is the local path;
   the container is started once per session and torn down afterwards.
3. Skip. Integration tests are skipped rather than failed when neither is
   available, so ``make check`` stays green on a machine without Docker while
   still running the full suite wherever Docker exists.

Everything session-scoped is created lazily, so a pure unit-test run
(``make test-unit``) never starts a container.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

TIMESCALE_IMAGE = "timescale/timescaledb-ha:pg16"
CONNECT_TIMEOUT_SECONDS = 5


# =============================================================================
# Configuration
# =============================================================================
@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Return the repository root directory."""
    from pathlib import Path  # noqa: PLC0415

    return Path(__file__).resolve().parent.parent


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Return a fresh Settings instance isolated from the developer's .env.

    Clears the ``get_settings`` cache before and after, so a test that patches
    the environment cannot leak configuration into the next one.
    """
    from energy.core.settings import Settings, get_settings  # noqa: PLC0415

    monkeypatch.setenv("APP_ENV", "ci")
    monkeypatch.setenv("APP_DEBUG", "false")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")

    get_settings.cache_clear()
    instance = Settings()
    yield instance
    get_settings.cache_clear()


# =============================================================================
# Database
# =============================================================================
def _is_reachable(url: str) -> bool:
    """Return whether a database URL accepts a connection right now."""
    try:
        engine = create_engine(url, connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS})
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
    except (SQLAlchemyError, OSError):
        return False
    return True


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Return a URL for a live TimescaleDB database, or skip.

    Yields:
        A SQLAlchemy DSN. When a container was started for the session it is
        stopped once the session ends.
    """
    env_url = os.environ.get("DATABASE_URL", "")
    if env_url and _is_reachable(env_url):
        yield env_url
        return

    try:
        from testcontainers.postgres import PostgresContainer  # noqa: PLC0415
    except ImportError:  # pragma: no cover - testcontainers is a dev dependency
        pytest.skip("testcontainers is not installed and DATABASE_URL is not reachable")

    container = None
    try:
        container = PostgresContainer(
            image=TIMESCALE_IMAGE,
            username="energy",
            password="energy",  # ephemeral test container
            dbname="energy",
            driver="psycopg",
        )
        container.start()
    except Exception as exc:  # any Docker failure means "skip"
        # A container that started but did not answer in time is still running.
        if container is not None:
            with contextlib.suppress(Exception):
                container.stop()
        pytest.skip(f"cannot start a TimescaleDB test container: {exc}")

    try:
        yield container.get_connection_url()
    finally:
        container.stop()


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    """Return an engine bound to the test database."""
    test_engine = create_engine(database_url, future=True)
    try:
        yield test_engine
    finally:
        test_engine.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    """Return a session wrapped in a transaction that is always rolled back.

    Each test therefore starts from the same state regardless of what the
    previous one wrote, without the cost of recreating the schema.
    """
    from sqlalchemy.orm import sessionmaker  # noqa: PLC0415

    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def patched_settings_db(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Point the application's own settings and engine at the test database.

    Needed by tests that exercise application code which resolves its own
    connection (health probes, the ``/ready`` endpoint) rather than receiving a
    session.
    """
    from energy.core.settings import get_settings  # noqa: PLC0415
    from energy.db.session import dispose_engine  # noqa: PLC0415

    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    dispose_engine()
    try:
        yield database_url
    finally:
        dispose_engine()
        get_settings.cache_clear()


@pytest.fixture
def migrated_database(database_url: str) -> Iterator[str]:
    """Apply every migration to the test database, then tear the schema down.

    Yields:
        The URL of a database at Alembic head.
    """
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    from energy.core.constants import ALL_SCHEMAS  # noqa: PLC0415

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url

    from pathlib import Path  # noqa: PLC0415

    root = Path(__file__).resolve().parent.parent
    # `toml_file=` rather than the positional `file_`: the positional argument is
    # parsed as INI, and pyproject.toml's repeated [[tool.mypy.overrides]] tables
    # are valid TOML but a duplicate section to configparser.
    config = Config(toml_file=str(root / "pyproject.toml"))
    config.set_main_option("script_location", str(root / "migrations"))

    try:
        command.upgrade(config, "head")
        yield database_url
    finally:
        cleanup_engine = create_engine(database_url, future=True)
        with cleanup_engine.begin() as connection:
            for schema in reversed(ALL_SCHEMAS):
                connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
        cleanup_engine.dispose()

        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


BRONZE_REVISION = "0002"
SILVER_REVISION = "0003"


def _engine_at(database_url: str, revision: str) -> Iterator[Engine]:
    """Yield an engine for a database migrated to one revision, then empty it.

    Args:
        database_url: the test database.
        revision: the Alembic revision to stop at.

    Yields:
        An engine for that database.
    """
    from pathlib import Path  # noqa: PLC0415

    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    from energy.core.constants import ALL_SCHEMAS  # noqa: PLC0415

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url

    root = Path(__file__).resolve().parent.parent
    config = Config(toml_file=str(root / "pyproject.toml"))
    config.set_main_option("script_location", str(root / "migrations"))

    engine = create_engine(database_url, future=True)
    try:
        command.upgrade(config, revision)
        yield engine
    finally:
        with engine.begin() as connection:
            for schema in reversed(ALL_SCHEMAS):
                connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
        engine.dispose()
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture(scope="class")
def bronze_engine(database_url: str) -> Iterator[Engine]:
    """Return an engine bound to a database migrated to the Bronze revision only.

    Class-scoped: applying the migrations is by far the slowest step of the
    integration suite, and the Bronze tests only read the schema, land rows they
    roll back, or build on one shared load, so a class can share one database.

    Yields:
        An engine for a database holding the ``bronze`` schema and nothing else.
    """
    yield from _engine_at(database_url, BRONZE_REVISION)


@pytest.fixture(scope="class")
def silver_engine(database_url: str) -> Iterator[Engine]:
    """Return an engine bound to a database migrated up to the Silver revision.

    Class-scoped, for the same reason as :func:`bronze_engine`. The database
    holds Bronze, the reference dimensions and Silver, and no Gold.

    Yields:
        An engine for that database.
    """
    yield from _engine_at(database_url, SILVER_REVISION)


# =============================================================================
# API
# =============================================================================
@pytest.fixture
def app() -> FastAPI:
    """Return a FastAPI application built for testing."""
    from energy.api.main import create_app  # noqa: PLC0415
    from energy.core.settings import Settings  # noqa: PLC0415

    return create_app(Settings())


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """Return a test client for the API.

    ``raise_server_exceptions=False`` so that a handler raising
    ``NotImplementedError`` is observed as the 501 response the exception
    handler produces, rather than propagating into the test.
    """
    from fastapi.testclient import TestClient  # noqa: PLC0415

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
