"""Application services of the Gold layer of the equivalent-batteries data.

Layer: services

Responsibility:
    Owns the three use cases of EQUS Gold. The calculation itself is done by
    the store behind :class:`~energy.ports.equs_gold.GoldReader`; what belongs
    here is the order of the steps and the decisions between them.

    :class:`GoldRefresher`
        Brings what Gold reads up to date: optionally Silver first, then the two
        conversion tables, then the planner statistics, all under the lock that
        keeps two refreshes from overlapping. It never touches a closed month.

    :class:`MonthCloser`
        Closes a month: refresh, run the data-quality checks, refuse when a
        blocking one has rows unless the warnings are accepted, freeze, and say
        how the month compares with the previous closed one.

    :class:`RangeReporter`
        Gathers what a report of any date range is made of, live or from the
        frozen close, and refuses the questions that have no honest answer: a
        month no version of the equation covers, or the frozen close of a month
        that was not closed whole.

Depends on:
    ``energy.ports``, ``energy.domain``, ``energy.core``.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from energy.core.logging import get_logger
from energy.domain.equs_gold import (
    CLOSE_TOLERANCE,
    REPORT_SOURCES,
    ClosedDifference,
    CloseSummary,
    DateRange,
    RefreshStep,
    ReportData,
    VersionMonth,
    month_label,
)
from energy.domain.exceptions import CloseRefusedError, ConfigurationError, InvalidPeriodError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date, datetime

    from energy.domain.equs_gold import DriverAmount, QualityCount, ReportSource
    from energy.domain.equs_silver import SilverBuildReport
    from energy.ports.equs_gold import GoldMaintenance, GoldReader
    from energy.ports.equs_silver import SilverBuildRepository

logger = get_logger(__name__)

_ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class RefreshOutcome:
    """What a refresh did.

    Attributes:
        steps: each step and how long it took, in order.
        silver_report: the report of the Silver build, when it ran.
    """

    steps: tuple[RefreshStep, ...]
    silver_report: SilverBuildReport | None = None


class GoldRefresher:
    """Brings what the EQUS Gold views read up to date.

    Args:
        maintenance: the store's maintenance operations.
        silver: the Silver build, for a refresh that starts from Bronze.
    """

    def __init__(
        self, maintenance: GoldMaintenance, silver: SilverBuildRepository | None = None
    ) -> None:
        self._maintenance = maintenance
        self._silver = silver

    def refresh(
        self, *, with_silver: bool = False, concurrently: bool = True, lock_timeout: float
    ) -> RefreshOutcome:
        """Refresh, under the lock.

        Args:
            with_silver: build Silver from Bronze first, in its own transaction.
            concurrently: refresh the conversion tables without blocking readers.
            lock_timeout: seconds to wait for another refresh or close to finish.

        Returns:
            The steps and their times, and the Silver report if it was built.

        Raises:
            LockTimeoutError: if the lock was not released in time.
            ConfigurationError: if a Silver build is asked for but not wired.
            PipelineError, RepositoryError: if a step fails.
        """
        if with_silver and self._silver is None:
            raise ConfigurationError("no Silver build is wired to this refresh")
        steps: list[RefreshStep] = []
        silver_report: SilverBuildReport | None = None
        waiting = time.perf_counter()
        with self._maintenance.locked(lock_timeout):
            steps.append(RefreshStep("take the refresh lock", time.perf_counter() - waiting))
            if with_silver and self._silver is not None:
                started = time.perf_counter()
                silver_report = self._silver.build()
                steps.append(
                    RefreshStep(
                        "build Silver from Bronze",
                        time.perf_counter() - started,
                        "one transaction",
                    )
                )
            steps.extend(self._maintenance.refresh_views(concurrently=concurrently))
            steps.append(self._maintenance.analyze())
        logger.debug("equs gold refreshed", steps=[step.name for step in steps])
        return RefreshOutcome(tuple(steps), silver_report)


class MonthCloser:
    """Freezes the EQ of a month as its official close.

    Args:
        maintenance: the store's maintenance operations.
        reader: the store's read-only questions.
    """

    def __init__(self, maintenance: GoldMaintenance, reader: GoldReader) -> None:
        self._maintenance = maintenance
        self._reader = reader

    def close(
        self,
        month: date,
        *,
        closed_by: str,
        accept_warnings: bool = False,
        reclose: bool = False,
        lock_timeout: float,
        on_quality: Callable[[Sequence[QualityCount]], None] | None = None,
    ) -> CloseSummary:
        """Close a month.

        The conversion tables are refreshed first: this runs in a process of its
        own, so nothing has refreshed them for it.

        Args:
            month: the first day of the month.
            closed_by: who closes it.
            accept_warnings: close even when a blocking check has rows; the
                decision is recorded with the close.
            reclose: replace an earlier close of the month; its rows are kept
                in the history.
            lock_timeout: seconds to wait for another refresh or close.
            on_quality: called with the data-quality counts as soon as they are
                known, before the close is refused or written.

        Returns:
            The close, with the month's EQ and the previous closed month's.

        Raises:
            InvalidPeriodError: if no version of the equation covers the month.
            CloseRefusedError: if two versions cover it, if it is closed and
                ``reclose`` is false, if it has nothing to close, or if a
                blocking check has rows and the warnings were not accepted.
            LockTimeoutError: if the lock was not released in time.
            RepositoryError: if the database fails. Nothing is written.
        """
        period = DateRange.of_month(month)
        with self._maintenance.locked(lock_timeout):
            self._maintenance.refresh_views(concurrently=True)
            versions = [item.version for item in self._reader.versions(period)]
            if None in versions:
                raise InvalidPeriodError(
                    "no version of the equation covers the month", month=month_label(month)
                )
            if len(versions) > 1:
                raise CloseRefusedError(
                    "two versions of the equation are in force in the month; fix their "
                    "validity in silver.equs_version first",
                    month=month_label(month),
                    versions=versions,
                )

            quality = self._reader.quality(period)
            if on_quality is not None:
                on_quality(quality)
            blocking = [count for count in quality if count.bloqueante and count.filas]
            if blocking and not accept_warnings:
                raise CloseRefusedError(
                    "blocking data-quality checks have rows; correct them, or close with "
                    "--accept-warnings to record that they were accepted",
                    month=month_label(month),
                    checks={count.vista: count.filas for count in blocking},
                )
            frozen = self._maintenance.freeze_month(
                month,
                closed_by=closed_by,
                reclose=reclose,
                accepted_warnings=bool(blocking),
                quality=quality,
            )

        processes = self._reader.closed_processes(month)
        previous = self._reader.previous_closed_month(month)
        previous_processes = self._reader.closed_processes(previous) if previous else ()
        return CloseSummary(
            frozen=frozen,
            quality=quality,
            processes=processes,
            equs_batt=sum((equs for _, equs in processes), start=_ZERO),
            equs_poly=sum((item.equs for item in self._reader.poly(period)), start=_ZERO),
            previous_month=previous,
            previous_processes=previous_processes,
            previous_equs_batt=(
                sum((equs for _, equs in previous_processes), start=_ZERO) if previous else None
            ),
        )


class RangeReporter:
    """Gathers the rows of a report of the equivalent batteries of any date range.

    Args:
        reader: the store's read-only questions.
    """

    def __init__(self, reader: GoldReader) -> None:
        self._reader = reader

    def report(
        self,
        period: DateRange,
        *,
        source: ReportSource = "live",
        by_month: bool = False,
        generated_at: datetime,
    ) -> ReportData:
        """Gather a report.

        Args:
            period: the days, both ends included.
            source: ``live`` computes from Silver now; ``closed`` reads the
                frozen close, and needs whole months that are all closed.
            by_month: add the month-by-month breakdown.
            generated_at: when the report is made.

        Returns:
            The report's rows, ready for :func:`~energy.domain.equs_gold.nest_report`.

        Raises:
            InvalidPeriodError: if the source is unknown, if a month of the
                range has no version of the equation, or if the frozen close is
                asked for months that are partial or not closed.
            ConfigurationError: if two versions are in force in one month.
            RepositoryError: if the database fails.
        """
        if source not in REPORT_SOURCES:
            raise InvalidPeriodError("the source is live or closed", source=source)

        if source == "closed":
            amounts = self._closed_amounts(period)
            versions_by_month = {(amount.month, amount.version) for amount in amounts}
            versions_used = sorted({version for _, version in versions_by_month})
            drivers = self._reader.drivers(versions_used)
            names = {driver.version: driver.version_name for driver in drivers}
            versions = tuple(
                VersionMonth(month, version, names.get(version))
                for month, version in sorted(versions_by_month)
            )
            differences: tuple[ClosedDifference, ...] = ()
        else:
            versions = self._live_versions(period)
            amounts = self._reader.amounts(period, closed=False)
            drivers = self._reader.drivers(
                sorted({item.version for item in versions if item.version is not None})
            )
            differences = self._differences_from_close(period, amounts)

        return ReportData(
            period=period,
            source=source,
            generated_at=generated_at,
            versions=versions,
            drivers=drivers,
            amounts=amounts,
            poly=self._reader.poly(period),
            quality=self._reader.quality(period),
            closed_differences=differences,
            by_month=by_month,
        )

    def _live_versions(self, period: DateRange) -> tuple[VersionMonth, ...]:
        versions = self._reader.versions(period)
        uncovered = [month_label(item.month) for item in versions if item.version is None]
        if uncovered:
            raise InvalidPeriodError(
                "no version of the equation covers part of the range; the first version "
                "starts later, or a gap was left between versions",
                months=uncovered,
            )
        overlapping = sorted(
            month_label(month)
            for month, count in Counter(item.month for item in versions).items()
            if count > 1
        )
        if overlapping:
            raise ConfigurationError(
                "two versions of the equation are in force in the same month; fix their "
                "validity in silver.equs_version",
                months=overlapping,
            )
        return versions

    def _closed_amounts(self, period: DateRange) -> tuple[DriverAmount, ...]:
        closed = set(self._reader.closed_months(period))
        partial = set(period.partial_months)
        not_closed = [m for m in period.months if m not in closed and m not in partial]
        if partial or not_closed:
            raise InvalidPeriodError(
                "the frozen close can only answer whole months that are all closed",
                partial_months=[month_label(m) for m in sorted(partial)],
                months_not_closed=[month_label(m) for m in not_closed],
            )
        return self._reader.amounts(period, closed=True)

    def _differences_from_close(
        self, period: DateRange, live: Sequence[DriverAmount]
    ) -> tuple[ClosedDifference, ...]:
        partial = set(period.partial_months)
        closed = [month for month in self._reader.closed_months(period) if month not in partial]
        if not closed:
            return ()
        frozen = {
            (amount.month, amount.cuenta_stats): amount.equs
            for amount in self._reader.amounts(
                DateRange(closed[0], DateRange.of_month(closed[-1]).end), closed=True
            )
            if amount.month in closed
        }
        computed = {
            (amount.month, amount.cuenta_stats): amount.equs
            for amount in live
            if amount.month in closed
        }
        differences: list[ClosedDifference] = []
        for month, cuenta in sorted(set(frozen) | set(computed)):
            now = computed.get((month, cuenta), _ZERO)
            then = frozen.get((month, cuenta), _ZERO)
            if abs(now - then) > CLOSE_TOLERANCE:
                differences.append(ClosedDifference(month, cuenta, now, then))
        return tuple(differences)


__all__ = ["GoldRefresher", "MonthCloser", "RangeReporter", "RefreshOutcome"]
