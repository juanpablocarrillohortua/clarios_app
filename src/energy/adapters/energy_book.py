"""The monthly energy of the plant areas, read from ``Base_de_datos.xlsx``.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.energy_cross.AreaEnergySource` over the
    plant's monthly energy workbook: one sheet per area, one row per month and
    process. It reads each area's *total* row (``Proceso`` equal to the area's
    own name) and its ``KWH`` column, never the ``KWH por proceso`` allocation
    of the rows below it. The sheets keep two rows above the data: notes, then
    the headers, which are found by their text.

    It answers by month only. The labels and the year of each December are
    read by :mod:`energy.domain.energy_book`; clipping to a range by
    :func:`energy.domain.energy_cross.monthly_area_energy`. The file is read
    once per source object and never written.

Depends on:
    ``energy.ports``, ``energy.domain``, ``energy.adapters.calamine_workbook``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.domain.energy_book import BookRow, book_months
from energy.domain.energy_cross import AreaEnergy, monthly_area_energy
from energy.domain.exceptions import (
    ConfigurationError,
    InvalidPeriodError,
    SourceUnavailableError,
    UnsupportedFileFormatError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date
    from pathlib import Path

    from energy.domain.equs_gold import DateRange
    from energy.domain.landing import CellValue
    from energy.ports.workbook_reader import WorkbookReader

YEAR_HEADER: Final = "Año"
MONTH_HEADER: Final = "Mes"
PROCESS_HEADER: Final = "Proceso"
KWH_HEADER: Final = "KWH"
# The headers are on the second row; look a little further in case a note row is added.
_HEADER_SEARCH_ROWS: Final = 5


@dataclass(frozen=True, slots=True)
class BookSheet:
    """One area of the workbook.

    Attributes:
        area: the plant area, as the EQ data names it (``Ensamble``).
        sheet: the sheet holding it.
        total_row: the ``Proceso`` of its total row.
    """

    area: str
    sheet: str
    total_row: str


def _text(cell: CellValue) -> str:
    return " ".join(str(cell).split()) if cell is not None else ""


def _number(cell: CellValue) -> Decimal | None:
    if isinstance(cell, bool) or not isinstance(cell, int | float):
        return None
    return Decimal(repr(cell)) if isinstance(cell, float) else Decimal(cell)


class EnergyBookSource:
    """The monthly energy of the areas the workbook has sheets for.

    Args:
        path: the workbook.
        sheets: one entry per area.
        reader: the workbook reader; calamine when ``None``.

    Raises:
        ConfigurationError: if two entries claim the same area.
    """

    def __init__(
        self, path: Path, sheets: Sequence[BookSheet], reader: WorkbookReader | None = None
    ) -> None:
        by_area: dict[str, BookSheet] = {}
        for sheet in sheets:
            if sheet.area in by_area:
                raise ConfigurationError("two sheets for one area", area=sheet.area)
            by_area[sheet.area] = sheet
        self._path = path
        self._sheets = by_area
        self._reader = reader or CalamineWorkbookReader()
        self._months: dict[str, dict[date, Decimal | None]] = {}

    @property
    def areas(self) -> tuple[str, ...]:
        """Return the areas with a sheet, in the order given."""
        return tuple(self._sheets)

    def energy(self, area: str, period: DateRange) -> AreaEnergy:
        """Return the energy of one area by whole month over a range.

        Raises:
            InvalidPeriodError: if the workbook has no sheet for the area.
            SourceUnavailableError: if the workbook cannot be read.
            UnsupportedFileFormatError: if a header is missing or the months
                of the sheet cannot be read.
        """
        if area not in self._sheets:
            raise InvalidPeriodError("no sheet of the energy workbook for this area", area=area)
        return monthly_area_energy(area, self._path.name, self.months(area), period)

    def months(self, area: str) -> dict[date, Decimal | None]:
        """Return the kWh of each month of one area, reading the workbook once."""
        if area not in self._months:
            self._months[area] = self._read(self._sheets[area])
        return self._months[area]

    def _read(self, sheet: BookSheet) -> dict[date, Decimal | None]:
        try:
            data = self._path.read_bytes()
        except OSError as exc:
            raise SourceUnavailableError(
                "energy workbook cannot be read", path=str(self._path), reason=str(exc)
            ) from exc
        found = self._reader.open_workbook(data).sheet(sheet.sheet)
        rows = list(found.rows)
        wanted = (YEAR_HEADER, MONTH_HEADER, PROCESS_HEADER, KWH_HEADER)
        headers = [[_text(cell) for cell in row] for row in rows[:_HEADER_SEARCH_ROWS]]
        index = next(
            (i for i, header in enumerate(headers) if all(name in header for name in wanted)),
            None,
        )
        if index is None:
            raise UnsupportedFileFormatError(
                "energy sheet lacks a header", sheet=sheet.sheet, wanted=list(wanted)
            )
        header = headers[index]
        year, month, process, kwh = (header.index(name) for name in wanted)

        total_rows = []
        for number, row in enumerate(rows[index + 1 :], start=found.first_row + index + 1):
            if _text(row[process]) != sheet.total_row:
                continue
            year_value = _number(row[year])
            if year_value is None:
                raise UnsupportedFileFormatError(
                    "energy row has no year", sheet=sheet.sheet, row=number
                )
            total_rows.append(
                BookRow(number, int(year_value), _text(row[month]), _number(row[kwh]))
            )
        try:
            return book_months(total_rows)
        except ValueError as exc:  # a year no date can hold
            raise UnsupportedFileFormatError(
                "energy sheet has an impossible date", sheet=sheet.sheet, reason=str(exc)
            ) from exc


__all__ = ["BookSheet", "EnergyBookSource"]
