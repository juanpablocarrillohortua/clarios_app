"""The equivalent batteries of any date range, as JSON organised like RESUMEN EQU.

Layer: pipelines

Responsibility:
    The workbook answers one question: the equivalent batteries (EQ) of one
    month. :func:`build_report` answers it for any range of days - a week, a
    quarter, a fiscal year to date, 15 March to 10 April - and returns a
    dictionary laid out as sheet RESUMEN EQU is: plants, processes and drivers,
    EQUS BATT, EQUS POLY, then the informative drivers, with the data-quality
    counts of the range and the warnings a reader needs.

    It holds no business rule. The rules are in the database: the functions of
    revision 0004 apply ``silver.instrucc`` and ``silver.factores_equ``, and
    ``gold.secciones_reporte`` and ``gold.filas_resumen_equ`` give the layout.
    This module only wires the reader to the use case and arranges the result.

    The command ``equs-report`` (see :mod:`energy.__main__`) parses its options,
    calls :func:`build_report` and writes the dictionary as JSON. The JSON
    Schema the output follows sits next to this file, at :data:`SCHEMA_PATH`.

Depends on:
    ``energy.services.equs_gold``, ``energy.adapters.postgres_equs_gold``,
    ``energy.domain``, ``energy.core``.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from energy.adapters.postgres_equs_gold import PostgresGoldReader
from energy.core.settings import get_settings
from energy.db.session import get_engine
from energy.domain.equs_gold import DateRange, nest_report, parse_day
from energy.domain.exceptions import ConfigurationError, RepositoryError
from energy.services.equs_gold import RangeReporter

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine

    from energy.domain.equs_gold import ReportSource

#: The JSON Schema every report follows.
SCHEMA_PATH: Final = Path(__file__).with_name("equs_report.schema.json")


def _day(value: date | str, name: str) -> date:
    return value if isinstance(value, date) else parse_day(value, name=name)


def build_report(
    conn: Connection | Engine,
    start: date | str,
    end: date | str,
    source: ReportSource = "live",
    by_month: bool = False,
) -> dict[str, Any]:
    """Compute the equivalent batteries of a date range.

    Args:
        conn: where to read. A connection is used inside its own transaction,
            so a report can see changes that are not committed yet; an engine
            gives each question a short connection of its own.
        start: the first day, a date or ``YYYY-MM-DD``. Included.
        end: the last day, a date or ``YYYY-MM-DD``. Included.
        source: ``live`` computes from Silver now; ``closed`` reads the frozen
            close and needs whole months that are all closed. EQUS POLY is
            never frozen and is always computed.
        by_month: add a breakdown by month inside each subsection.

    Returns:
        The report, ready for :func:`json.dumps`.

    Raises:
        InvalidPeriodError: if a date does not parse, the start is later than
            the end, a month of the range has no version of the equation, or
            the closed source is asked for months that are partial or not
            closed.
        ConfigurationError: if two versions of the equation overlap.
        RepositoryError: if the database fails.
    """
    period = DateRange(_day(start, "--start"), _day(end, "--end"))
    settings = get_settings().equs
    reader = PostgresGoldReader(conn, variation_threshold=settings.variation_threshold)
    data = RangeReporter(reader).report(
        period,
        source=source,
        by_month=by_month,
        generated_at=datetime.now(ZoneInfo(settings.timezone)),
    )
    return nest_report(data)


def engine_for(dsn: str | None) -> Engine:
    """Return the engine a report reads through.

    Args:
        dsn: a database URL such as ``postgresql://user:password@host:5432/db``;
            the configured database when ``None``. A plain ``postgresql://`` URL
            is given the driver the application uses.

    Returns:
        The engine.

    Raises:
        ConfigurationError: if the text is not a database URL.
    """
    if dsn is None:
        return get_engine()
    try:
        url = make_url(dsn)
    except ArgumentError as exc:
        raise ConfigurationError(
            "--dsn is not a database URL; write it as postgresql://user:password@host:port/name"
        ) from exc
    if url.drivername in ("postgresql", "postgres"):
        url = url.set(drivername="postgresql+psycopg")
    return create_engine(url, pool_pre_ping=True)


def report_for(
    start: date | str,
    end: date | str,
    source: ReportSource = "live",
    by_month: bool = False,
    *,
    dsn: str | None = None,
) -> dict[str, Any]:
    """Compute a report on a connection of its own, as the command line does.

    Args:
        start: the first day, included.
        end: the last day, included.
        source: ``live`` or ``closed``.
        by_month: add the month-by-month breakdown.
        dsn: the database URL; the configured database when ``None``.

    Returns:
        The report.

    Raises:
        ConfigurationError: if ``dsn`` is not a database URL.
        RepositoryError: if the database cannot be reached or fails.
        InvalidPeriodError: see :func:`build_report`.
    """
    engine = engine_for(dsn)
    try:
        with engine.connect() as connection:
            return build_report(connection, start, end, source, by_month)
    except SQLAlchemyError as exc:
        raise RepositoryError(
            "could not reach the database", reason=str(getattr(exc, "orig", exc)).strip()
        ) from exc
    finally:
        if dsn is not None:
            engine.dispose()


def load_schema() -> dict[str, Any]:
    """Return the JSON Schema a report follows."""
    schema: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return schema


__all__ = ["SCHEMA_PATH", "build_report", "engine_for", "load_schema", "report_for"]
