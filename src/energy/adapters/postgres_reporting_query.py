"""The chat assistant's queries on the real ``reporting`` views, in PostgreSQL.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.reporting_query.ReportingQuery` with the
    read-only reporting login (``reporting_reader``, created by ``energy
    reporting create-reader``), never the application's own. That login can
    read the views of schema ``reporting`` and nothing else, and its
    transactions are read-only by default; the connection asks for both again,
    as a second lock on the same door.

    Each query runs on the driver's own cursor, so the model's SQL is sent as
    written (a ``%`` in it is never taken for a parameter), in a transaction
    that is declared read-only, carries a ``statement_timeout``, and is always
    rolled back. At most the rows asked for are fetched, plus one to tell
    whether there were more.

    Used only when ``CHAT_DATA_SOURCE=database``: the plant must first agree
    that rows of these views may reach the model's vendor.

Depends on:
    ``energy.domain``, ``energy.ports``, ``sqlalchemy``, ``psycopg``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import psycopg
from psycopg import errors
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from energy.domain.exceptions import (
    DatabaseUnavailableError,
    MissingRelationError,
    QueryFailedError,
    QueryTimeoutError,
)
from energy.ports.reporting_query import QueryResult

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.engine import URL

    from energy.ports.reporting_query import DataSourceKind

SCHEMA: Final = "reporting"

# What every connection asks for, whatever the login's own defaults say.
_CONNECT_OPTIONS: Final = "-c default_transaction_read_only=on -c search_path=reporting"
_CONNECT_TIMEOUT_SECONDS: Final = 10

_DESCRIBE: Final = text(
    "SELECT table_name, column_name, data_type FROM information_schema.columns "
    "WHERE table_schema = :schema ORDER BY table_name, ordinal_position"
)


def _first_line(exc: Exception) -> str:
    message = str(exc).strip()
    return message.splitlines()[0] if message else type(exc).__name__


class PostgresReportingQuery:
    """The ``reporting`` views, read with the read-only reporting login."""

    def __init__(self, url: URL) -> None:
        """Prepare the connection pool; nothing connects until the first query.

        Args:
            url: the database, with the reporting login's name and password.
        """
        self._engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=2,
            connect_args={"options": _CONNECT_OPTIONS, "connect_timeout": _CONNECT_TIMEOUT_SECONDS},
        )

    @property
    def data_source(self) -> DataSourceKind:
        """The rows come from the database."""
        return "database"

    def describe(self) -> Mapping[str, tuple[tuple[str, str], ...]]:
        """Return every view of ``reporting`` the login can read, with its columns.

        Raises:
            DatabaseUnavailableError: if the database cannot be reached.
        """
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(_DESCRIBE, {"schema": SCHEMA}).all()
        except SQLAlchemyError as exc:
            msg = "the reporting views cannot be read"
            raise DatabaseUnavailableError(msg, reason=_first_line(exc)) from exc
        described: dict[str, list[tuple[str, str]]] = {}
        for table, column, kind in rows:
            described.setdefault(str(table), []).append((str(column), str(kind)))
        return {table: tuple(columns) for table, columns in described.items()}

    def run(self, sql: str, *, max_rows: int, timeout_seconds: float) -> QueryResult:
        """Run one read-only statement and return its first rows.

        Raises:
            MissingRelationError: if it names a table or view that does not exist.
            QueryTimeoutError: if it runs longer than ``timeout_seconds``.
            QueryFailedError: if PostgreSQL refuses it or cannot run it.
            DatabaseUnavailableError: if the database cannot be reached.
        """
        milliseconds = str(max(1, round(timeout_seconds * 1000)))
        rows: list[Any] = []
        columns: tuple[str, ...] = ()
        try:
            with self._engine.connect() as connection:
                driver = connection.connection.driver_connection
                if not isinstance(driver, psycopg.Connection):
                    msg = "the reporting login is not on a psycopg connection"
                    raise DatabaseUnavailableError(msg)
                try:
                    with driver.cursor() as cursor:
                        cursor.execute("SET TRANSACTION READ ONLY")
                        cursor.execute(
                            "SELECT set_config('statement_timeout', %s, true)", (milliseconds,)
                        )
                        # Sent as written: without parameters, psycopg reads no placeholder.
                        cursor.execute(sql)  # nosec B608
                        if cursor.description is not None:
                            columns = tuple(column.name for column in cursor.description)
                            rows = cursor.fetchmany(max_rows + 1)
                finally:
                    driver.rollback()
        except errors.UndefinedTable as exc:
            msg = "the query names a table that does not exist"
            raise MissingRelationError(msg, reason=_first_line(exc)) from exc
        except errors.QueryCanceled as exc:
            msg = "the query took too long"
            raise QueryTimeoutError(msg, seconds=timeout_seconds) from exc
        except psycopg.Error as exc:
            msg = "the query could not be run"
            raise QueryFailedError(msg, reason=_first_line(exc)) from exc
        except SQLAlchemyError as exc:
            msg = "the reporting views cannot be read"
            raise DatabaseUnavailableError(msg, reason=_first_line(exc)) from exc
        return QueryResult(
            columns=columns,
            rows=tuple(tuple(row) for row in rows[:max_rows]),
            truncated=len(rows) > max_rows,
        )

    def dispose(self) -> None:
        """Close the pool's connections."""
        self._engine.dispose()


__all__ = ["PostgresReportingQuery"]
