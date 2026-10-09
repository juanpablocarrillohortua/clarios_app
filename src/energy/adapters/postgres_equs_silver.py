"""PostgreSQL implementation of the Silver build of the equivalent-batteries data.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.equs_silver.SilverBuildRepository` against
    the ``bronze`` and ``silver`` schemas. The work is the SQL of
    :mod:`energy.adapters.equs_silver_sql`; this module only orchestrates it:
    it runs the statements in order inside one transaction, refreshes the two
    conversion tables, reads the data-quality report while the staging tables
    still exist, and commits. If any step fails the whole build rolls back, so
    Silver is never left half built.

    The statements are sent through the driver exactly as written. They are
    fixed text with no values bound in, and several hold more than one command,
    which only the simple query protocol accepts.

Depends on:
    ``energy.ports.equs_silver``, ``energy.domain``, ``energy.core``,
    ``sqlalchemy``, ``psycopg``.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Final

import psycopg
from sqlalchemy.exc import SQLAlchemyError

from energy.adapters import equs_silver_sql as sql
from energy.core.logging import get_logger
from energy.domain.equs_silver import (
    CategoryConflict,
    ItemQuantity,
    LineWithoutPlant,
    PolyMismatch,
    SeveralAssembled,
    SilverBuildReport,
    StructureSheet,
)
from energy.domain.exceptions import PipelineError, RepositoryError

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine

logger = get_logger(__name__)

_COMPLETED_RUNS: Final = "SELECT count(*) FROM bronze.ingestion_runs WHERE state = 'completed'"

# One count per Silver table and view. The names are the module's own constant,
# so nothing from outside reaches this text.
_ROW_COUNTS: Final = " UNION ALL ".join(
    f"SELECT '{table}', count(*) FROM silver.{table}"  # noqa: S608  # nosec B608
    for table in sql.SILVER_TABLES
)


class PostgresSilverBuildRepository:
    """Builds the EQUS Silver tables from Bronze, in PostgreSQL.

    Args:
        engine: the SQLAlchemy engine to work through.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def build(self) -> SilverBuildReport:
        """Rebuild Silver from the Bronze rows of completed runs.

        Returns:
            What the build did to the data and what needs attention.

        Raises:
            PipelineError: if Bronze holds no completed run. Nothing is changed.
            RepositoryError: if the database rejects a step. Nothing is changed.
        """
        started = time.perf_counter()
        try:
            with self._engine.begin() as connection:
                driver_connection = connection.connection.driver_connection
                if driver_connection is None:  # pragma: no cover - a live connection has one
                    raise RepositoryError("database connection is closed")
                with driver_connection.cursor() as cursor:
                    self._require_bronze(cursor)
                    self._run_steps(cursor)
                    concurrent = self._refresh_views(connection, cursor)
                    report = self._read_report(cursor, concurrent)
        except (SQLAlchemyError, psycopg.Error) as exc:
            raise RepositoryError("could not build Silver", reason=str(exc).strip()) from exc

        seconds = time.perf_counter() - started
        logger.debug("silver built", seconds=round(seconds, 1), rows=dict(report.rows))
        return replace(report, seconds=seconds)

    @staticmethod
    def _require_bronze(cursor: psycopg.Cursor[Any]) -> None:
        """Refuse to build from an empty Bronze, which would empty Silver."""
        cursor.execute(_COMPLETED_RUNS)
        row = cursor.fetchone()
        if row is None or row[0] == 0:
            raise PipelineError(
                "Bronze holds no completed load, so there is nothing to build Silver from; "
                "land the workbooks first"
            )

    @staticmethod
    def _run_steps(cursor: psycopg.Cursor[Any]) -> None:
        """Run the build statements in order."""
        for step in sql.BUILD_STEPS:
            step_started = time.perf_counter()
            try:
                cursor.execute(step.sql)
            except psycopg.Error as exc:
                raise RepositoryError(
                    "a step of the Silver build failed and nothing was changed",
                    step=step.name,
                    reason=str(exc).strip(),
                ) from exc
            logger.debug(
                "silver step done",
                step=step.name,
                seconds=round(time.perf_counter() - step_started, 2),
            )

    @staticmethod
    def _refresh_views(connection: Connection, cursor: psycopg.Cursor[Any]) -> bool:
        """Refresh the conversion tables, without blocking readers when possible.

        Args:
            connection: the connection the build runs on.
            cursor: its driver cursor.

        Returns:
            Whether the concurrent refresh was used. PostgreSQL refuses it for a
            view that was never populated, and then the plain refresh runs
            instead; the attempt sits in a savepoint so that its failure does
            not doom the build.
        """
        try:
            with connection.begin_nested():
                for statement in sql.REFRESH_VIEWS_CONCURRENTLY:
                    cursor.execute(statement)
        except psycopg.Error as exc:
            logger.debug("concurrent refresh refused", reason=str(exc).strip())
        else:
            return True

        try:
            for statement in sql.REFRESH_VIEWS:
                cursor.execute(statement)
        except psycopg.Error as exc:
            raise RepositoryError(
                "could not refresh the conversion tables and nothing was changed",
                reason=str(exc).strip(),
            ) from exc
        return False

    @staticmethod
    def _read_report(cursor: psycopg.Cursor[Any], concurrent: bool) -> SilverBuildReport:
        """Read the data-quality report while the staging tables still exist."""

        def rows(statement: str) -> list[tuple[Any, ...]]:
            try:
                cursor.execute(statement)
                return cursor.fetchall()
            except psycopg.Error as exc:
                raise RepositoryError(
                    "could not read the Silver build report and nothing was changed",
                    reason=str(exc).strip(),
                ) from exc

        sheets, datasets, receipts, invoice_lines, item_master_rows, line_groups = rows(
            sql.REPORT_SOURCES
        )[0]
        receipts_left_out, invoice_lines_left_out = rows(sql.REPORT_MOVEMENTS_LEFT_OUT)[0]

        notes: list[str] = []
        if sheets == 0:
            notes.append("No product structure is landed: the structure is empty.")
        if receipts == 0:
            notes.append("No production receipt is landed.")
        if invoice_lines == 0:
            notes.append("No invoice line is landed.")
        if item_master_rows == 0:
            notes.append(
                "Item_Master is not landed: items are described by the other workbooks, "
                "and no item has a plate family."
            )
        elif rows(sql.REPORT_ITEM_MASTER_KEYS)[0][0] == 0:
            notes.append(
                "Item_Master is landed but none of its rows has the header "
                "'Número Artículo': it was not used. Its header names need to be mapped."
            )
        if line_groups == 0:
            notes.append(
                "L.PRODUCCION is not landed (it is loaded only with --include-optional): "
                "no production line has a group."
            )
        if datasets == 0:
            notes.append("No hand-maintained catalogue is landed.")

        # The database returns the counts in no particular order.
        counts = dict(rows(_ROW_COUNTS))

        return SilverBuildReport(
            rows={table: counts[table] for table in sql.SILVER_TABLES},
            structure=tuple(StructureSheet(*row) for row in rows(sql.REPORT_STRUCTURE)),
            receipts_left_out=receipts_left_out,
            invoice_lines_left_out=invoice_lines_left_out,
            placeholders=dict(rows(sql.REPORT_PLACEHOLDERS)),
            items_without_description=tuple(
                row[0] for row in rows(sql.REPORT_ITEMS_WITHOUT_DESCRIPTION)
            ),
            lines_without_plant=tuple(
                LineWithoutPlant(*row) for row in rows(sql.REPORT_LINES_WITHOUT_PLANT)
            ),
            poly_factor_mismatches=tuple(
                PolyMismatch(*row) for row in rows(sql.REPORT_POLY_MISMATCHES)
            ),
            poly_repeated_items=tuple(row[0] for row in rows(sql.REPORT_POLY_REPEATED)),
            formed_without_paste=tuple(
                ItemQuantity(*row) for row in rows(sql.REPORT_FORMED_WITHOUT_PASTE)
            ),
            decorated_without_pieces=tuple(
                ItemQuantity(*row) for row in rows(sql.REPORT_DECORATED_WITHOUT_PIECES)
            ),
            na_with_poly_category=tuple(
                CategoryConflict(*row) for row in rows(sql.REPORT_NA_WITH_POLY_CATEGORY)
            ),
            formed_with_several_assembled=tuple(
                SeveralAssembled(*row) for row in rows(sql.REPORT_FORMED_WITH_SEVERAL_ASSEMBLED)
            ),
            notes=tuple(notes),
            concurrent_refresh=concurrent,
        )


__all__ = ["PostgresSilverBuildRepository"]
