"""SQLAlchemy engine and session factory.

Layer: db (infrastructure)

Creates exactly one :class:`~sqlalchemy.engine.Engine` per process, configured
from :class:`~energy.core.settings.DatabaseSettings`, and hands out sessions
through a context manager that commits on success and rolls back on failure.

Nothing in this module knows about tables. Repository adapters receive a
``Session`` and do the mapping; the API layer gets one session per request via
``energy.api.deps``.
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from energy.core.logging import get_logger
from energy.core.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

logger = get_logger(__name__)

# Applied to every new connection. ``UTC`` guarantees that any driver-side
# timestamp rendering matches the canonical grid, regardless of server locale.
_CONNECTION_SETUP_SQL = "SET TIME ZONE 'UTC'"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy engine.

    The engine is created on first use and cached. ``pool_pre_ping`` is enabled
    so connections severed by a database restart or an idle-timeout proxy are
    transparently replaced instead of surfacing as errors.

    Returns:
        A configured :class:`~sqlalchemy.engine.Engine` bound to the DSN from
        settings. Never ``None``; connection failures surface on first use, not
        here.
    """
    settings = get_settings()
    db = settings.database

    engine = create_engine(
        db.url,
        echo=db.echo,
        pool_size=db.pool_size,
        max_overflow=db.max_overflow,
        pool_timeout=db.pool_timeout,
        pool_pre_ping=True,
        future=True,
        connect_args={"application_name": settings.app_name},
    )

    @event.listens_for(engine, "connect")
    def _set_session_timezone(dbapi_connection: Any, _connection_record: Any) -> None:
        with dbapi_connection.cursor() as cursor:
            cursor.execute(_CONNECTION_SETUP_SQL)

    logger.debug("database engine created", dsn=db.safe_url, pool_size=db.pool_size)
    return engine


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    """Return the process-wide session factory.

    Returns:
        A :class:`~sqlalchemy.orm.sessionmaker` bound to the engine from
        :func:`get_engine`, with autoflush enabled and ``expire_on_commit``
        disabled so that objects stay usable after the session closes.
    """
    return sessionmaker(
        bind=get_engine(),
        class_=Session,
        autoflush=True,
        autocommit=False,
        expire_on_commit=False,
    )


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional session scope.

    Commits when the block exits normally, rolls back when it raises, and always
    closes the session.

    Yields:
        An open :class:`~sqlalchemy.orm.Session`.

    Raises:
        Exception: whatever the wrapped block raised, after rolling back.

    Example:
        >>> with session_scope() as session:  # doctest: +SKIP
        ...     session.execute(text("SELECT 1"))
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("database session rolled back")
        raise
    finally:
        session.close()


def ping(session: Session | None = None) -> bool:
    """Execute a trivial query to confirm the database answers.

    Args:
        session: an existing session to reuse. When omitted, a short-lived one is
            opened and closed around the query.

    Returns:
        ``True`` when ``SELECT 1`` succeeds. Exceptions are propagated rather
        than swallowed - callers that want a boolean-only probe should use
        :func:`energy.db.health.check_connectivity`.
    """
    if session is not None:
        session.execute(text("SELECT 1"))
        return True
    with session_scope() as scoped:
        scoped.execute(text("SELECT 1"))
    return True


def dispose_engine() -> None:
    """Dispose the engine and clear the cached factories.

    Call this on application shutdown, and in tests that swap the database URL
    between cases, so pooled connections are not leaked across event loops.
    """
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    logger.debug("database engine disposed")


__all__ = ["dispose_engine", "get_engine", "get_sessionmaker", "ping", "session_scope"]
