"""Port for reading the cells of a workbook.

Layer: ports

Responsibility:
    Declares how the application gets at the contents of a spreadsheet file: the
    names of its sheets, and the rows of one sheet as plain cell values. The
    contract is deliberately thin. A reader returns the values the workbook has
    cached - never formulas - and reports where the used range of a sheet
    starts, so the caller can name rows and columns the way Excel does. It does
    not skip rows, rename columns or convert values; those are landing rules and
    belong to :mod:`energy.domain.landing`.

Depends on:
    ``energy.domain``, standard library typing only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from energy.domain.landing import CellValue


@dataclass(frozen=True, slots=True)
class SheetRange:
    """The used range of one worksheet.

    Attributes:
        first_row: Excel row number, 1-based, of the first row in ``rows``. It is
            greater than 1 when the sheet starts with empty rows.
        first_column: 0-based index of the column every row starts at; ``0`` is
            column A. It is greater than 0 when the sheet starts with empty
            columns.
        rows: the rows of the range, top to bottom, all of the same width. An
            empty cell is ``None`` or an empty string.
    """

    first_row: int
    first_column: int
    rows: Iterator[Sequence[CellValue]]


@runtime_checkable
class OpenWorkbook(Protocol):
    """A workbook whose sheets can be read."""

    @property
    def sheet_names(self) -> tuple[str, ...]:
        """Return every sheet name, in workbook order, hidden sheets included."""
        ...

    def sheet(self, name: str) -> SheetRange:
        """Return the used range of one sheet.

        Args:
            name: the sheet name, exactly as :attr:`sheet_names` spells it.

        Returns:
            The sheet's :class:`SheetRange`. The rows are read lazily.

        Raises:
            EntityNotFoundError: if the workbook has no sheet of that name.
        """
        ...


@runtime_checkable
class WorkbookReader(Protocol):
    """Interface for opening spreadsheet files."""

    def open_workbook(self, data: bytes) -> OpenWorkbook:
        """Open a workbook from its bytes.

        Taking the bytes rather than a path guarantees that what is parsed is
        exactly what was hashed and archived.

        Args:
            data: the file content.

        Returns:
            An :class:`OpenWorkbook`.

        Raises:
            UnsupportedFileFormatError: if the bytes are not a readable workbook.
        """
        ...


@runtime_checkable
class WorkbookFileReader(Protocol):
    """Interface for opening a workbook kept in a local file.

    The streaming counterpart of :class:`WorkbookReader`: a file of up to
    300 MB is never held in memory, so it is copied from the archive into a
    temporary file, its hash checked on the way, and read there. A zip
    archive keeps its directory at its end, so a workbook cannot be read from
    a stream that only goes forward.
    """

    def open_workbook_file(self, path: Path) -> OpenWorkbook:
        """Open a workbook from a local file, reading its parts in place.

        Args:
            path: the file. It must stay in place while the workbook is read.

        Returns:
            An :class:`OpenWorkbook`.

        Raises:
            UnsupportedFileFormatError: if the file is not a readable workbook.
        """
        ...


__all__ = ["OpenWorkbook", "SheetRange", "WorkbookFileReader", "WorkbookReader"]
