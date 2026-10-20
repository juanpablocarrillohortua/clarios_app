"""The chat assistant's queries on simulated reporting data, in DuckDB.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.reporting_query.ReportingQuery` on an
    in-memory DuckDB database holding the simulated views of
    :mod:`energy.adapters.simulated_reporting`, as tables of schema
    ``reporting``. DuckDB's SQL is close to PostgreSQL's (``date_trunc``,
    ``::`` casts, ``FILTER``, ``ILIKE``), so a query that works here should work
    on the real views.

    The guards, set once, before any query:

    * no access to files or the network, and no extension loaded or
      installed (``enable_external_access``, the ``autoload`` and
      ``autoinstall`` options);
    * ``lock_configuration``, so a query cannot undo those settings.

    And for each query: its own cursor, whose ``search_path`` is ``reporting``
    (so ``equs_mensual`` and ``reporting.equs_mensual`` both work), inside a
    transaction that is always rolled back; a timer that interrupts it at the
    time limit; at most the rows asked for, plus one to tell whether there
    were more.

Depends on:
    ``energy.domain``, ``energy.ports``, ``duckdb``, ``pandas``.
"""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING, Final

import duckdb
import pandas as pd

from energy.adapters.simulated_reporting import simulated_views
from energy.domain.exceptions import MissingRelationError, QueryFailedError, QueryTimeoutError
from energy.ports.reporting_query import QueryResult

if TYPE_CHECKING:
    from collections.abc import Mapping

    from energy.ports.reporting_query import DataSourceKind

SCHEMA: Final = "reporting"

# DuckDB's words for a table that does not exist (CatalogException also covers
# unknown functions and types, which are the query's own mistake).
_MISSING_TABLE: Final = "Table with name"


def _first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__


class DuckDBReportingQuery:
    """Simulated ``reporting`` views in an in-memory DuckDB database."""

    def __init__(self) -> None:
        """Build the database, fill it with the simulation, and lock it down."""
        self._database = duckdb.connect(
            ":memory:",
            config={"autoinstall_known_extensions": False, "autoload_known_extensions": False},
        )
        self._database.execute(f"CREATE SCHEMA {SCHEMA}")
        for name, view in simulated_views().items():
            columns = ", ".join(f"{column} {kind}" for column, kind in view.columns)
            # Names and types are this package's constants, never input.
            self._database.execute(f"CREATE TABLE {SCHEMA}.{name} ({columns})")
            # One insert per table from a registered frame: row by row takes seconds.
            frame = pd.DataFrame.from_records(
                list(view.rows), columns=[column for column, _ in view.columns]
            )
            self._database.register("incoming", frame)
            self._database.execute(
                f"INSERT INTO {SCHEMA}.{name} SELECT * FROM incoming"  # noqa: S608  # nosec B608
            )
            self._database.unregister("incoming")
        self._columns = self._read_columns()
        self._database.execute("SET enable_external_access = false")
        self._database.execute("SET lock_configuration = true")

    @property
    def data_source(self) -> DataSourceKind:
        """The rows are simulated."""
        return "simulated"

    def _read_columns(self) -> dict[str, tuple[tuple[str, str], ...]]:
        rows = self._database.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = ? ORDER BY table_name, ordinal_position",
            [SCHEMA],
        ).fetchall()
        described: dict[str, list[tuple[str, str]]] = {}
        for table, column, kind in rows:
            described.setdefault(str(table), []).append((str(column), str(kind)))
        return {table: tuple(columns) for table, columns in described.items()}

    def describe(self) -> Mapping[str, tuple[tuple[str, str], ...]]:
        """Return every simulated view with its columns and their types, in order."""
        return dict(self._columns)

    def run(self, sql: str, *, max_rows: int, timeout_seconds: float) -> QueryResult:
        """Run one read-only statement on the simulation and return its first rows.

        Raises:
            MissingRelationError: if it names a table that does not exist.
            QueryTimeoutError: if it runs longer than ``timeout_seconds``.
            QueryFailedError: if DuckDB refuses it or cannot run it.
        """
        cursor = self._database.cursor()
        timer = threading.Timer(timeout_seconds, cursor.interrupt)
        try:
            cursor.execute(f"SET search_path = '{SCHEMA}'")
            cursor.execute("BEGIN TRANSACTION")
            timer.start()
            try:
                cursor.execute(sql)
                rows = cursor.fetchmany(max_rows + 1)
                description = cursor.description or []
            finally:
                timer.cancel()
        except duckdb.InterruptException as exc:
            msg = "the query took too long"
            raise QueryTimeoutError(msg, seconds=timeout_seconds) from exc
        except duckdb.CatalogException as exc:
            if _MISSING_TABLE in str(exc):
                msg = "the query names a table that does not exist"
                raise MissingRelationError(msg, reason=_first_line(exc)) from exc
            msg = "the query could not be run"
            raise QueryFailedError(msg, reason=_first_line(exc)) from exc
        except duckdb.Error as exc:
            msg = "the query could not be run"
            raise QueryFailedError(msg, reason=_first_line(exc)) from exc
        finally:
            # Nothing a query did is kept. Without a transaction left, there is nothing to undo.
            with contextlib.suppress(duckdb.Error):
                cursor.execute("ROLLBACK")
            cursor.close()
        return QueryResult(
            columns=tuple(str(column[0]) for column in description),
            rows=tuple(tuple(row) for row in rows[:max_rows]),
            truncated=len(rows) > max_rows,
        )


__all__ = ["DuckDBReportingQuery"]
