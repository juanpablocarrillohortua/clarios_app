"""Port for the read-only queries the chat assistant runs on reporting data.

Layer: ports

Responsibility:
    Declares how the assistant reads data: the views it may query and their
    columns, and the rows one ``SELECT`` returns. Two adapters implement it,
    chosen by ``CHAT_DATA_SOURCE``: the real ``reporting`` views through the
    read-only reporting login, and synthetic rows of the same shape in memory.
    Both run every query in a transaction that is rolled back, under a time
    limit, and return at most the rows asked for.

Depends on:
    ``energy.domain``, standard library typing only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Where an adapter's rows come from.
DataSourceKind = Literal["simulated", "database"]


@dataclass(frozen=True, slots=True)
class QueryResult:
    """The rows of one query.

    Attributes:
        columns: the column names, in order.
        rows: the rows, each a tuple in the order of ``columns``.
        truncated: whether the query had more rows than were returned.
    """

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool


@runtime_checkable
class ReportingQuery(Protocol):
    """Read-only access to the views of schema ``reporting``, or to their simulation."""

    @property
    def data_source(self) -> DataSourceKind:
        """Return where the rows come from: ``simulated`` or ``database``."""
        ...

    def describe(self) -> Mapping[str, tuple[tuple[str, str], ...]]:
        """Return every view of ``reporting`` with its columns and their types, in order."""
        ...

    def run(self, sql: str, *, max_rows: int, timeout_seconds: float) -> QueryResult:
        """Run one read-only statement and return its first rows.

        Args:
            sql: one statement, already checked by
                :func:`~energy.domain.chat_sql.check_query`.
            max_rows: the most rows returned.
            timeout_seconds: how long it may run.

        Returns:
            The rows, and whether there were more.

        Raises:
            MissingRelationError: if it names a table or view that does not exist.
            QueryTimeoutError: if it runs longer than ``timeout_seconds``.
            QueryFailedError: if the database refuses it or cannot run it.
        """
        ...


__all__ = ["DataSourceKind", "QueryResult", "ReportingQuery"]
