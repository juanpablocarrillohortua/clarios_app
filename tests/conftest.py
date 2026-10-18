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
# The release docker-compose.yml pins, pulled from MinIO's own registry: the
# Docker Hub mirror refuses anonymous pulls.
MINIO_IMAGE = "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z"
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
    from energy.core.settings import get_settings  # noqa: PLC0415

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    # The migrations read the address from the cached settings. Something
    # imported earlier may have filled that cache with the address of `.env`.
    get_settings.cache_clear()

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
        get_settings.cache_clear()


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
    from energy.core.settings import get_settings  # noqa: PLC0415

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    # As in `migrated_database`: the migrations read the cached settings.
    get_settings.cache_clear()

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
        get_settings.cache_clear()


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


@pytest.fixture(scope="class")
def gold_engine(database_url: str) -> Iterator[Engine]:
    """Return an engine bound to a database migrated to head, Gold included.

    Class-scoped, for the same reason as :func:`bronze_engine`. Each class gets
    a database of its own, so a class that commits a close does not leak it
    into another.

    Yields:
        An engine for that database.
    """
    yield from _engine_at(database_url, "head")


# =============================================================================
# Object storage
# =============================================================================
@pytest.fixture(scope="session")
def minio_settings() -> Iterator[Any]:
    """Return the settings of a MinIO container started once per session, or skip.

    Yields:
        :class:`~energy.core.settings.MinioSettings` pointing at the container.
    """
    try:
        from testcontainers.minio import MinioContainer  # noqa: PLC0415
    except ImportError:  # pragma: no cover - testcontainers is a dev dependency
        pytest.skip("testcontainers is not installed")
    from energy.core.settings import MinioSettings  # noqa: PLC0415

    container = None
    try:
        container = MinioContainer(image=MINIO_IMAGE)
        container.start()
    except Exception as exc:  # any Docker failure means "skip"
        # A container that started but did not answer in time is still running.
        if container is not None:
            with contextlib.suppress(Exception):
                container.stop()
        pytest.skip(f"cannot start a MinIO test container: {exc}")

    try:
        config = container.get_config()
        yield MinioSettings(
            _env_file=None,
            MINIO_ENDPOINT=config["endpoint"],
            MINIO_ROOT_USER=config["access_key"],
            MINIO_ROOT_PASSWORD=config["secret_key"],
            MINIO_SECURE=False,
        )
    finally:
        container.stop()


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


@pytest.fixture
def token_verifier(app: FastAPI) -> Iterator[Any]:
    """Replace the API's token verifier with one that knows only the tokens it is given.

    No test may reach the identity provider, so the bound adapter is swapped for
    a stand-in through the dependency the API reads it from. The stand-in has
    two methods beside the port's ``verify``: ``issue(*roles)`` registers a
    token for a caller with those role values and returns it, and
    ``refuse_all()`` makes it answer as if the provider could not be reached.
    """
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from energy.api.deps import get_token_verifier  # noqa: PLC0415
    from energy.domain.exceptions import (  # noqa: PLC0415
        AuthenticationError,
        IdentityProviderUnavailableError,
    )
    from energy.ports.token_verifier import VerifiedToken  # noqa: PLC0415

    class FakeTokenVerifier:
        """A token verifier that accepts exactly the tokens it issued."""

        subject = "00000000-0000-4000-8000-00000000abcd"
        tenant_id = "00000000-0000-4000-8000-0000000000aa"

        def __init__(self) -> None:
            self._issued: dict[str, VerifiedToken] = {}
            self._unreachable = False

        def issue(self, *roles: str, name: str | None = "Ada Lovelace") -> str:
            token = f"test-token-{len(self._issued)}"
            self._issued[token] = VerifiedToken(
                subject=self.subject,
                tenant_id=self.tenant_id,
                name=name,
                username="ada@example.test" if name else None,
                roles=tuple(roles),
                expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
            )
            return token

        def refuse_all(self) -> None:
            self._unreachable = True

        def verify(self, token: str) -> VerifiedToken:
            if self._unreachable:
                raise IdentityProviderUnavailableError("the signing keys could not be read")
            try:
                return self._issued[token]
            except KeyError:
                raise AuthenticationError(
                    "the access token was refused", reason="bad_signature"
                ) from None

    fake = FakeTokenVerifier()
    app.dependency_overrides[get_token_verifier] = lambda: fake
    try:
        yield fake
    finally:
        app.dependency_overrides.pop(get_token_verifier, None)


@pytest.fixture
def auth_headers(token_verifier: Any) -> Any:
    """Return a function that gives the request headers of a signed-in caller.

    ``auth_headers("viewer")`` is a caller with that role; ``auth_headers()`` is
    a caller who is signed in and has no role.
    """

    def _headers(*roles: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token_verifier.issue(*roles)}"}

    return _headers
