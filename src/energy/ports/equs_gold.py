"""Ports for the Gold layer of the equivalent-batteries data.

Layer: ports

Responsibility:
    Declares what the application needs from wherever the Gold layer of the
    equivalent-batteries (EQUS) calculation lives. Two interfaces, because the
    two kinds of work have different shapes:

    :class:`GoldReader`
        Read-only questions about a period: which version of the equation
        applies, what each driver summed and its EQ, EQUS POLY, the
        data-quality counts, which months are closed. Every answer comes from
        the same source of truth, so a month and a range covering it agree.

    :class:`GoldMaintenance`
        The work that changes something: the lock that keeps two refreshes or
        closes from running at once, refreshing the conversion tables Gold
        reads, refreshing statistics, and freezing a month.

    :class:`DailyGoldReader`
        The same calculation day by day: what each driver summed on each day,
        and whether the days of a month add up to the month. A protocol of its
        own, so that a reader of months need not answer days.

    No protocol mentions SQL, a table or a view.

Depends on:
    ``energy.domain``, standard library typing only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from contextlib import AbstractContextManager
    from datetime import date
    from decimal import Decimal

    from energy.domain.equs_gold import (
        DateRange,
        Driver,
        DriverAmount,
        FrozenMonth,
        PolyAmount,
        QualityCount,
        RefreshStep,
        VersionMonth,
    )
    from energy.domain.equs_series import DailyDriverAmount, ReconciledAmount


@runtime_checkable
class GoldReader(Protocol):
    """Read-only access to the EQUS Gold calculation."""

    def versions(self, period: DateRange) -> tuple[VersionMonth, ...]:
        """Return the version of the equation in force in each month of a period.

        A month no version covers has ``version=None``; a month two versions
        cover appears twice.
        """
        ...

    def drivers(self, versions: Sequence[int]) -> tuple[Driver, ...]:
        """Return every driver of the given versions, with its place in the report."""
        ...

    def amounts(self, period: DateRange, *, closed: bool) -> tuple[DriverAmount, ...]:
        """Return what each driver summed in each month of a period, and its EQ.

        Args:
            period: the days, both ends included.
            closed: read the frozen close instead of computing. The caller makes
                sure the period is whole, closed months.

        Returns:
            One row per month and driver of the version in force, including
            drivers that summed nothing.
        """
        ...

    def poly(self, period: DateRange) -> tuple[PolyAmount, ...]:
        """Return EQUS POLY of a period, by month and category, computed live."""
        ...

    def quality(self, period: DateRange) -> tuple[QualityCount, ...]:
        """Return how many rows each data-quality check returns for a period."""
        ...

    def closed_months(self, period: DateRange) -> tuple[date, ...]:
        """Return the closed months a period touches."""
        ...

    def closed_processes(self, month: date) -> tuple[tuple[str, Decimal], ...]:
        """Return the frozen EQ of each process of a closed month that adds to EQUS BATT."""
        ...

    def previous_closed_month(self, month: date) -> date | None:
        """Return the closest closed month before a month, if any."""
        ...

    def latest_production_month(self) -> date | None:
        """Return the latest month with production receipts, if any."""
        ...


@runtime_checkable
class GoldMaintenance(Protocol):
    """The operations that change the EQUS Gold state."""

    def locked(self, timeout_seconds: float) -> AbstractContextManager[None]:
        """Hold the lock refreshes and closes share for the duration of a block.

        Raises:
            LockTimeoutError: if another run keeps it longer than the timeout.
        """
        ...

    def refresh_views(self, *, concurrently: bool = True) -> tuple[RefreshStep, ...]:
        """Recompute the materialized conversion tables Gold reads.

        Args:
            concurrently: refresh without blocking readers. A plain refresh
                is used instead where that is not possible.

        Returns:
            One step per view, with its time.
        """
        ...

    def analyze(self) -> RefreshStep:
        """Refresh the planner statistics of the tables the calculation reads."""
        ...

    def freeze_month(
        self,
        month: date,
        *,
        closed_by: str,
        reclose: bool,
        accepted_warnings: bool,
        quality: Sequence[QualityCount],
    ) -> FrozenMonth:
        """Freeze the live EQ of a month as its official close, in one transaction.

        Args:
            month: the first day of the month.
            closed_by: who closes it.
            reclose: replace an earlier close, keeping it in the history.
            accepted_warnings: blocking checks had rows and the person closing
                accepted them; recorded with the close.
            quality: the data-quality counts the close saw; recorded with it.

        Returns:
            What was written.

        Raises:
            CloseRefusedError: if the month is closed and ``reclose`` is false,
                or if it has nothing to close. Nothing is written.
        """
        ...


@runtime_checkable
class DailyGoldReader(Protocol):
    """Read-only access to the EQUS Gold calculation, day by day."""

    def daily_amounts(self, period: DateRange) -> tuple[DailyDriverAmount, ...]:
        """Return what each driver summed on each day of a period, and its EQ.

        Args:
            period: the days, both ends included.

        Returns:
            One row per day and driver that something fed, with the version in
            force in the day's month. A day nothing fed has no row.
        """
        ...

    def daily_reconciliation(self, months: DateRange) -> tuple[ReconciledAmount, ...]:
        """Compare each driver of whole months with the sum of its days.

        Args:
            months: whole months, from the first day of one to the last of another.

        Returns:
            One row per month and driver that the month or its days have, with
            both sides and whether they are equal, digit for digit.
        """
        ...


__all__ = ["DailyGoldReader", "GoldMaintenance", "GoldReader"]
