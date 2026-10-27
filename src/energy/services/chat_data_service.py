"""The chat assistant's tools for reading data: list sources, describe views, run SQL.

Layer: services

Responsibility:
    Gives the assistant's model three tools, each a method whose docstring the
    model reads and whose answer is JSON text:

    * :meth:`ChatDataService.list_sources`: which files of the plant are in the
      database (and through which views of schema ``reporting``), and which are
      still pending;
    * :meth:`ChatDataService.describe_reporting`: the views and their columns;
    * :meth:`ChatDataService.run_sql`: one read-only ``SELECT``.

    Every answer names its ``data_source``: ``simulated`` or ``database``. A
    query is checked (:func:`~energy.domain.chat_sql.check_query`) before it
    runs. A query that names a table that does not exist answers
    ``pending_implementation``: the data the question needs has no table yet.
    Any other refusal or failure answers ``error`` with a reason the model can
    act on; nothing here raises to the model's loop.

    Each query is logged with its SQL, its row count and its time. The question
    that led to it is never logged.

Depends on:
    ``energy.domain``, ``energy.ports``, ``energy.core``.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, Final

from energy.core.logging import get_logger
from energy.domain.chat_sources import SourceState
from energy.domain.chat_sql import check_query
from energy.domain.exceptions import (
    DatabaseUnavailableError,
    MissingRelationError,
    QueryFailedError,
    QueryRefusedError,
    QueryTimeoutError,
)

if TYPE_CHECKING:
    from energy.domain.chat_sources import SourceCatalogue
    from energy.ports.reporting_query import ReportingQuery

logger = get_logger(__name__)

#: What ``run_sql`` answers when the data the question needs has no table yet.
PENDING_IMPLEMENTATION: Final = "pending_implementation"

#: The line that ends every answer with a figure in simulated mode, word for word.
SIMULATED_NOTICE: Final = "(Datos simulados: no son cifras reales de la planta.)"

# Said to the model in simulated mode only. In the instructions it was always in
# view, and the model ended answers on real data with it too.
_SIMULATED_RULE: Final = (
    "The data is simulated: the figures are not real. End every answer that gives a figure "
    f'with this line, exactly: "{SIMULATED_NOTICE}"'
)

# How much of a query reaches the log.
_LOGGED_SQL_CHARS: Final = 2000
_NEWLINE: Final = chr(10)


def _json(value: Any) -> str:
    """Return JSON text; dates, times and decimals as their text."""
    return json.dumps(value, ensure_ascii=False, default=str)


class ChatDataService:
    """The read-only data tools of the assistant."""

    def __init__(
        self,
        query: ReportingQuery,
        catalogue: SourceCatalogue,
        *,
        row_limit: int,
        timeout_seconds: float,
    ) -> None:
        """Wire the tools.

        Args:
            query: where queries run: the simulation or the real views.
            catalogue: the plant's data sources and their state.
            row_limit: the most rows one query returns.
            timeout_seconds: how long one query may run.
        """
        self._query = query
        self._catalogue = catalogue
        self._row_limit = row_limit
        self._timeout_seconds = timeout_seconds

    @property
    def data_source(self) -> str:
        """Where the rows come from: ``simulated`` or ``database``."""
        return self._query.data_source

    def sources_note(self) -> str:
        """Return the catalogue as a note that goes with every question.

        The model then knows which data has a table before it calls any tool,
        so a question about a pending source gets its answer without a query.
        In simulated mode the note ends with the rule that marks every figure
        as simulated; in database mode it says nothing of it.
        """
        lines = [f"Data sources of the plant (data: {self.data_source}):"]
        for source in self._catalogue.sources:
            files = ", ".join(source.files)
            if source.state is SourceState.IMPLEMENTED:
                views = ", ".join(view.name for view in source.views)
                lines.append(f"- implemented: {source.description} Files: {files}. Views: {views}.")
            else:
                lines.append(f"- pending (no table yet): {source.description} Files: {files}.")
        if self.data_source == "simulated":
            lines.append(_SIMULATED_RULE)
        return _NEWLINE.join(lines)

    def list_sources(self) -> str:
        """List the plant's data files and whether each one is in the database.

        Call this first. A source in state "implemented" can be queried through the
        views of schema reporting listed with it. A source in state "pending" has no
        table yet: questions about it cannot be answered from data.
        """
        return _json(
            {
                "data_source": self.data_source,
                "sources": [
                    {
                        "key": source.key,
                        "files": list(source.files),
                        "description": source.description,
                        "state": source.state.value,
                        "views": [
                            {"name": view.name, "description": view.description}
                            for view in source.views
                        ],
                    }
                    for source in self._catalogue.sources
                ],
            }
        )

    def describe_reporting(self) -> str:
        """List the views of schema reporting with their columns and types.

        Call this before writing SQL, to use the real view and column names.
        """
        try:
            views = self._query.describe()
        except DatabaseUnavailableError as exc:
            return _json({"status": "error", "reason": exc.message})
        return _json(
            {
                "data_source": self.data_source,
                "schema": "reporting",
                "views": {
                    name: [{"column": column, "type": kind} for column, kind in columns]
                    for name, columns in views.items()
                },
            }
        )

    def run_sql(self, query: str) -> str:
        """Run one read-only SELECT on the views of schema reporting and return its rows.

        Write PostgreSQL syntax. Only one statement, starting with SELECT or WITH. Aggregate
        in SQL (sum, group by) rather than fetching many rows: at most a limited number of
        rows come back, and "truncated" says when there were more.

        Args:
            query: the SELECT statement.
        """
        started = time.perf_counter()
        try:
            statement = check_query(query)
            result = self._query.run(
                statement, max_rows=self._row_limit, timeout_seconds=self._timeout_seconds
            )
        except MissingRelationError as exc:
            self._log("chat query names a missing table", query, started, code=exc.code)
            return _json(
                {
                    "status": PENDING_IMPLEMENTATION,
                    "reason": "the table this question needs does not exist yet",
                    "detail": exc.details.get("reason"),
                }
            )
        except (QueryRefusedError, QueryFailedError) as exc:
            self._log("chat query refused", query, started, code=exc.code)
            return _json({"status": "error", "reason": exc.details.get("reason", exc.message)})
        except QueryTimeoutError as exc:
            self._log("chat query timed out", query, started, code=exc.code)
            return _json(
                {"status": "error", "reason": "the query took too long; aggregate or filter more"}
            )
        except DatabaseUnavailableError as exc:
            self._log("chat query found no database", query, started, code=exc.code)
            return _json({"status": "error", "reason": "the database cannot be reached"})
        self._log(
            "chat query run",
            statement,
            started,
            rows=len(result.rows),
            truncated=result.truncated,
        )
        return _json(
            {
                "status": "ok",
                "data_source": self.data_source,
                "columns": list(result.columns),
                "rows": [list(row) for row in result.rows],
                "truncated": result.truncated,
            }
        )

    def _log(self, event: str, sql: str, started: float, **fields: Any) -> None:
        logger.info(
            event,
            data_source=self.data_source,
            sql=sql[:_LOGGED_SQL_CHARS],
            seconds=round(time.perf_counter() - started, 3),
            **fields,
        )


__all__ = ["PENDING_IMPLEMENTATION", "SIMULATED_NOTICE", "ChatDataService"]
