"""Immutable value objects: EnergyAmount and TimeWindow.

Layer: domain

Responsibility:
    Defines the two types that appear in almost every domain signature. Unlike
    entities, value objects have no identity: two ``EnergyAmount`` instances with
    the same magnitude and unit are interchangeable. They are frozen and they
    validate on construction, which is what makes unit errors and misaligned
    intervals impossible to represent rather than merely discouraged. This module
    performs no I/O and knows nothing about storage.

Depends on:
    ``energy.domain.exceptions``, ``energy.core.constants``, standard library.

Implemented in:
    Phase 2 - Domain model and KPI engine
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime, timedelta
    from decimal import Decimal


@dataclass(frozen=True, slots=True, order=True)
class EnergyAmount:
    """A quantity of energy or volume together with its unit.

    Arithmetic between amounts of different units raises rather than silently
    coercing: adding kWh to Nm3 is a bug, not a conversion. Conversion is always
    explicit, through :meth:`to`.

    Attributes:
        magnitude: the numeric quantity. ``Decimal`` rather than ``float`` so
            that billing-grade totals do not drift over millions of rows.
        unit: one of the canonical units in :mod:`energy.core.constants`
            (``kWh``, ``Nm3``, ``kW``).
    """

    magnitude: Decimal
    unit: str

    def __post_init__(self) -> None:
        """Validate the amount.

        Raises:
            UnitMismatchError: if ``unit`` is not a recognised canonical unit.
        """
        raise NotImplementedError("Phase 2: unit validation")

    def __add__(self, other: Self) -> Self:
        """Add two amounts of the same unit.

        Args:
            other: the amount to add.

        Returns:
            A new amount with the summed magnitude and the shared unit.

        Raises:
            UnitMismatchError: if the units differ.
        """
        raise NotImplementedError("Phase 2: unit-safe addition")

    def __sub__(self, other: Self) -> Self:
        """Subtract two amounts of the same unit.

        Args:
            other: the amount to subtract.

        Returns:
            A new amount with the difference. The result may be negative, which
            is meaningful for register rollovers and corrections.

        Raises:
            UnitMismatchError: if the units differ.
        """
        raise NotImplementedError("Phase 2: unit-safe subtraction")

    def to(self, unit: str, *, calorific_value: Decimal | None = None) -> Self:
        """Convert to another unit.

        The only conversion that needs external information is gas volume to
        energy, which requires the calorific value of the gas for the period.

        Args:
            unit: the target canonical unit.
            calorific_value: kWh per normal cubic metre, required when converting
                ``Nm3`` to ``kWh``.

        Returns:
            A new amount expressed in ``unit``.

        Raises:
            UnitMismatchError: if no conversion between the two units exists.
            ValueError: if ``calorific_value`` is required but not supplied.
        """
        raise NotImplementedError("Phase 2: unit conversion table")


@dataclass(frozen=True, slots=True, order=True)
class TimeWindow:
    """A half-open interval ``[start, end)`` on the canonical UTC grid.

    Half-open is deliberate: it makes consecutive windows tile the timeline
    without overlap, so summing readings over adjacent windows can never
    double-count the boundary instant.

    Attributes:
        start: interval start, inclusive. Timezone-aware UTC, aligned to the
            canonical grid.
        end: interval end, exclusive. Timezone-aware UTC, aligned to the
            canonical grid, and strictly after ``start``.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        """Validate the window.

        Raises:
            NaiveDatetimeError: if either bound is timezone-naive.
            InvalidTimeWindowError: if ``end`` is not strictly after ``start``,
                or if either bound is not aligned to the canonical grid.
        """
        raise NotImplementedError("Phase 2: window validation and grid alignment check")

    @property
    def duration(self) -> timedelta:
        """Return the length of the window."""
        raise NotImplementedError("Phase 2: window duration")

    @property
    def hours(self) -> Decimal:
        """Return the length of the window in hours, as a Decimal."""
        raise NotImplementedError("Phase 2: window length in hours")

    def contains(self, moment: datetime) -> bool:
        """Return whether an instant falls inside this window.

        Args:
            moment: a timezone-aware UTC datetime.

        Returns:
            ``True`` when ``start <= moment < end``.

        Raises:
            NaiveDatetimeError: if ``moment`` is timezone-naive.
        """
        raise NotImplementedError("Phase 2: instant membership test")

    def overlaps(self, other: Self) -> bool:
        """Return whether this window shares any instant with another.

        Args:
            other: the window to compare against.

        Returns:
            ``True`` when the two half-open intervals intersect.
        """
        raise NotImplementedError("Phase 2: window overlap test")

    def split(self, step: timedelta) -> Iterator[Self]:
        """Divide this window into consecutive sub-windows.

        Args:
            step: the length of each sub-window. Must divide the window exactly.

        Yields:
            Consecutive sub-windows tiling ``[start, end)``.

        Raises:
            InvalidTimeWindowError: if ``step`` does not divide the window
                exactly, which would leave a ragged final interval.
        """
        raise NotImplementedError("Phase 2: window subdivision")


__all__ = ["EnergyAmount", "TimeWindow"]
