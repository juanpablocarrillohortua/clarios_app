"""The rules of the plant's monthly energy workbook (``Base_de_datos.xlsx``).

Layer: domain

Responsibility:
    The workbook keeps one sheet per plant area, one row per month and process,
    with the area's measured kWh and its allocation to the processes inside it.
    This module turns the rows of one area's *total* row into the kWh of each
    month, and holds the two rules that takes:

    **Month labels.** The month is a three-letter name, in Spanish on most sheets
    (``Ene``, ``Abr``, ``Dic``) and in English on others (``Jan``, ``Apr``,
    ``Dec``); both are read.

    **The year of a December.** The year column is the calendar year, except
    that some sheets label December with the year that follows it: on OREC the
    row ``2024 Dic`` comes after ``2023 Nov`` and before ``2024 Ene``, so it is
    December 2023. A December takes the year of the November before it. The
    months must then follow one another without a gap or a repeat, which is
    checked, so a sheet that breaks the rule in a new way is refused rather than
    read wrong.

    Only the measured total of an area is read (its ``KWH`` column). The
    ``KWH por proceso`` column of the process rows is an allocation by fixed
    shares, not a measurement, and is never used.

Depends on:
    ``energy.domain.exceptions``, standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

from energy.domain.exceptions import DomainRuleError

if TYPE_CHECKING:
    from collections.abc import Iterable
    from decimal import Decimal

NOVEMBER: Final = 11
DECEMBER: Final = 12

MONTH_NUMBERS: Final = {
    **dict.fromkeys(("ene", "jan"), 1),
    "feb": 2,
    "mar": 3,
    **dict.fromkeys(("abr", "apr"), 4),
    "may": 5,
    "jun": 6,
    "jul": 7,
    **dict.fromkeys(("ago", "aug"), 8),
    "sep": 9,
    "oct": 10,
    "nov": 11,
    **dict.fromkeys(("dic", "dec"), 12),
}


@dataclass(frozen=True, slots=True)
class BookRow:
    """One total row of an area's sheet, as the workbook writes it.

    Attributes:
        row: the Excel row number, for error messages.
        year: the year column.
        month: the month label, as written.
        kwh: the measured kWh of the area; ``None`` if blank or not a number.
    """

    row: int
    year: int
    month: str
    kwh: Decimal | None


def month_number(label: str) -> int:
    """Return the number of a Spanish or English month label.

    Raises:
        DomainRuleError: if the label is not a month.
    """
    number = MONTH_NUMBERS.get(label.strip().lower()[:3])
    if number is None:
        raise DomainRuleError("not a month label", label=label)
    return number


def book_months(rows: Iterable[BookRow]) -> dict[date, Decimal | None]:
    """Return the kWh of each month of an area's total rows.

    Args:
        rows: the total rows, in sheet order.

    Returns:
        The kWh of each month, by its first day, in order.

    Raises:
        DomainRuleError: if a label is not a month, or the months do not follow
            one another without gaps or repeats once each December takes the
            year of the November before it.
    """
    months: dict[date, Decimal | None] = {}
    november_year: int | None = None
    previous: date | None = None
    for item in rows:
        number = month_number(item.month)
        year = item.year
        if number == NOVEMBER:
            november_year = year
        elif number == DECEMBER and november_year is not None:
            year = november_year
        month = date(year, number, 1)
        if previous is not None and month != _next_month(previous):
            raise DomainRuleError(
                "the months of the sheet do not follow one another",
                row=item.row,
                previous=previous.isoformat(),
                month=month.isoformat(),
            )
        months[month] = item.kwh
        previous = month
    return months


def _next_month(month: date) -> date:
    return (
        date(month.year + 1, 1, 1)
        if month.month == DECEMBER
        else month.replace(month=month.month + 1)
    )


__all__ = ["MONTH_NUMBERS", "BookRow", "book_months", "month_number"]
