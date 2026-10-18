"""Refresh, read and close the Gold layer of the equivalent-batteries (EQUS) data.

Layer: pipelines

Responsibility:
    The composition root of EQUS Gold. It wires the services of
    :mod:`energy.services.equs_gold` to the PostgreSQL adapter and the
    settings, for the four things a person does with Gold:

    * :func:`refresh_gold` brings what the Gold views read up to date, after new
      workbooks were landed: optionally Silver first, then the two conversion
      tables and the statistics;
    * :func:`resumen_equ` reads column H of RESUMEN EQU for one month, live or
      as it was closed;
    * :func:`close_month` freezes a month as its official close;
    * :func:`battery_equivalents` reads the EQ of the battery processes day by
      day or month by month.

    Like the Bronze load and the Silver build, these are plain functions rather
    than Prefect flows, so they run from a shell; a scheduled flow can wrap them
    unchanged.

Depends on:
    ``energy.services.equs_gold``, ``energy.adapters``, ``energy.db.session``,
    ``energy.pipelines.flows.equs_report``, ``energy.core``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from energy.adapters.postgres_equs_gold import PostgresGoldMaintenance, PostgresGoldReader
from energy.adapters.postgres_equs_silver import PostgresSilverBuildRepository
from energy.core.settings import get_settings
from energy.db.session import get_engine
from energy.domain.equs_gold import DateRange, column_h
from energy.pipelines.flows.equs_report import build_report
from energy.services.equs_gold import BatterySeries, GoldRefresher, MonthCloser

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date

    from sqlalchemy.engine import Engine

    from energy.domain.equs_gold import CloseSummary, ColumnHRow, QualityCount, ReportSource
    from energy.domain.equs_series import BatteryEquivalents, Frequency
    from energy.services.equs_gold import RefreshOutcome


def _lock_timeout(seconds: float | None) -> float:
    return seconds if seconds is not None else get_settings().equs.lock_timeout_seconds


def _reader(engine: Engine) -> PostgresGoldReader:
    return PostgresGoldReader(engine, variation_threshold=get_settings().equs.variation_threshold)


def refresh_gold(
    *,
    with_silver: bool = False,
    concurrently: bool = True,
    lock_timeout: float | None = None,
    engine: Engine | None = None,
) -> RefreshOutcome:
    """Bring what the EQUS Gold views read up to date.

    The views read Silver live, so they are stale only when Silver is behind
    Bronze or a conversion table is behind the structure. A closed month is
    never touched: it changes only through a reclose.

    Args:
        with_silver: build Silver from Bronze first, in its own transaction.
        concurrently: refresh the conversion tables without blocking readers.
        lock_timeout: seconds to wait for another refresh or close; the
            ``EQUS_LOCK_TIMEOUT_SECONDS`` setting when ``None``.
        engine: the database engine; the process-wide one when ``None``.

    Returns:
        The steps and their times, and the Silver report when it was built.

    Raises:
        LockTimeoutError: if another refresh or close kept the lock too long.
        PipelineError, RepositoryError: if a step fails.
    """
    bound = engine or get_engine()
    refresher = GoldRefresher(
        PostgresGoldMaintenance(bound),
        PostgresSilverBuildRepository(bound) if with_silver else None,
    )
    return refresher.refresh(
        with_silver=with_silver,
        concurrently=concurrently,
        lock_timeout=_lock_timeout(lock_timeout),
    )


def resumen_equ(
    month: date, *, source: ReportSource = "live", engine: Engine | None = None
) -> tuple[dict[str, Any], list[ColumnHRow]]:
    """Read column H of RESUMEN EQU for one month.

    Args:
        month: any day of the month.
        source: ``live`` computes it now; ``closed`` reads the month's close.
        engine: the database engine; the process-wide one when ``None``.

    Returns:
        The month's report, and its rows flattened in the order of the workbook.

    Raises:
        InvalidPeriodError: if no version covers the month, or the closed source
            is asked for a month that is not closed.
        RepositoryError: if the database fails.
    """
    period = DateRange.of_month(month)
    report = build_report(engine or get_engine(), period.start, period.end, source=source)
    return report, column_h(report)


def latest_production_month(engine: Engine | None = None) -> date | None:
    """Return the latest month with production receipts, if any."""
    return _reader(engine or get_engine()).latest_production_month()


def close_month(
    month: date,
    *,
    closed_by: str,
    accept_warnings: bool = False,
    reclose: bool = False,
    lock_timeout: float | None = None,
    on_quality: Callable[[Sequence[QualityCount]], None] | None = None,
    engine: Engine | None = None,
) -> CloseSummary:
    """Freeze a month's EQ as its official close.

    Args:
        month: the first day of the month.
        closed_by: who closes it.
        accept_warnings: close even when a blocking data-quality check has
            rows, recording that the warnings were accepted.
        reclose: replace an earlier close, keeping it in the history.
        lock_timeout: seconds to wait for another refresh or close; the
            ``EQUS_LOCK_TIMEOUT_SECONDS`` setting when ``None``.
        on_quality: called with the data-quality counts as soon as they are known.
        engine: the database engine; the process-wide one when ``None``.

    Returns:
        The close and how the month compares with the previous closed one.

    Raises:
        InvalidPeriodError: if no version covers the month.
        CloseRefusedError: if the close is refused; nothing is written.
        LockTimeoutError: if another refresh or close kept the lock too long.
        RepositoryError: if the database fails; nothing is written.
    """
    bound = engine or get_engine()
    closer = MonthCloser(PostgresGoldMaintenance(bound), _reader(bound))
    return closer.close(
        month,
        closed_by=closed_by,
        accept_warnings=accept_warnings,
        reclose=reclose,
        lock_timeout=_lock_timeout(lock_timeout),
        on_quality=on_quality,
    )


def battery_equivalents(
    period: DateRange,
    *,
    frequency: Frequency = "day",
    procesos: Sequence[str] = (),
    areas: Sequence[str] = (),
    engine: Engine | None = None,
) -> BatteryEquivalents:
    """Read the EQ of the battery processes over a range, by day or by month.

    Args:
        period: the days, both ends included.
        frequency: ``day`` or ``month``.
        procesos: keep only these battery processes; all when empty.
        areas: keep only the processes of these plant areas; all when empty.
        engine: the database engine; the process-wide one when ``None``.

    Returns:
        The series; a daily one carries the check of its whole months against
        ``gold.v_resumen_equ``.

    Raises:
        InvalidPeriodError: if the range or a filter cannot be answered.
        ConfigurationError: if two versions are in force in one month.
        RepositoryError: if the database fails.
    """
    reader = _reader(engine or get_engine())
    return BatterySeries(reader, reader).series(
        period, frequency=frequency, procesos=procesos, areas=areas
    )


__all__ = [
    "battery_equivalents",
    "close_month",
    "latest_production_month",
    "refresh_gold",
    "resumen_equ",
]
