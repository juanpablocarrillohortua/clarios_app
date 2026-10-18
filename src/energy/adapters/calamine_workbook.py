"""Workbook reader backed by calamine.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.workbook_reader.WorkbookReader` with
    ``python-calamine``, a Rust parser that reads the values Excel cached in the
    file - never the formulas - and is fast enough for exports of more than a
    hundred thousand rows. It is the only module that knows calamine exists.
    The adapter hands the cells over untouched: a number arrives as a float, a
    date as a date, an empty cell as an empty string. Turning them into text is
    a landing rule, not a reading concern.

Depends on:
    ``energy.ports.workbook_reader``, ``energy.domain.exceptions``,
    ``python-calamine``.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from python_calamine import CalamineError, CalamineWorkbook, WorksheetNotFound

from energy.domain.exceptions import EntityNotFoundError, UnsupportedFileFormatError
from energy.ports.workbook_reader import SheetRange

if TYPE_CHECKING:
    from pathlib import Path


class CalamineOpenWorkbook:
    """An open workbook.

    Args:
        workbook: the parsed calamine workbook.
    """

    def __init__(self, workbook: CalamineWorkbook) -> None:
        self._workbook = workbook

    @property
    def sheet_names(self) -> tuple[str, ...]:
        """Return every sheet name, in workbook order, hidden sheets included."""
        return tuple(self._workbook.sheet_names)

    def sheet(self, name: str) -> SheetRange:
        """Return the used range of one sheet.

        calamine yields rows from Excel row 1, padding any empty rows above the
        data, but drops the empty columns to its left. So the first row is
        always row 1 and only the first column has to be reported, which keeps
        a sheet that begins in column B on its real Excel coordinates.

        Args:
            name: the sheet name.

        Returns:
            The sheet's :class:`~energy.ports.workbook_reader.SheetRange`.

        Raises:
            EntityNotFoundError: if the workbook has no sheet of that name.
            UnsupportedFileFormatError: if the sheet cannot be parsed.
        """
        try:
            sheet = self._workbook.get_sheet_by_name(name)
        except WorksheetNotFound as exc:
            raise EntityNotFoundError(
                "workbook has no such sheet", sheet=name, available=list(self.sheet_names)
            ) from exc
        except CalamineError as exc:
            raise UnsupportedFileFormatError(
                "sheet cannot be read", sheet=name, reason=str(exc)
            ) from exc

        start = sheet.start
        if start is None:
            return SheetRange(first_row=1, first_column=0, rows=iter(()))
        _first_data_row, first_column = start
        return SheetRange(first_row=1, first_column=first_column, rows=sheet.iter_rows())


class CalamineWorkbookReader:
    """Opens ``.xlsx``, ``.xlsm``, ``.xls`` and ``.ods`` files.

    From bytes for the command-line loader, and from a local file for uploads
    too large to hold: calamine then reads each part of the archive in place,
    when it is reached.
    """

    def open_workbook(self, data: bytes) -> CalamineOpenWorkbook:
        """Open a workbook from its bytes.

        Args:
            data: the file content.

        Returns:
            The open workbook.

        Raises:
            UnsupportedFileFormatError: if the bytes are not a readable workbook.
        """
        try:
            workbook = CalamineWorkbook.from_filelike(io.BytesIO(data))
        except CalamineError as exc:
            raise UnsupportedFileFormatError(
                "file is not a readable workbook", reason=str(exc)
            ) from exc
        return CalamineOpenWorkbook(workbook)

    def open_workbook_file(self, path: Path) -> CalamineOpenWorkbook:
        """Open a workbook from a local file.

        Args:
            path: the file. It must stay in place while the workbook is read.

        Returns:
            The open workbook.

        Raises:
            UnsupportedFileFormatError: if the file is not a readable workbook.
        """
        try:
            workbook = CalamineWorkbook.from_path(str(path))
        except CalamineError as exc:
            raise UnsupportedFileFormatError(
                "file is not a readable workbook", reason=str(exc)
            ) from exc
        return CalamineOpenWorkbook(workbook)


__all__ = ["CalamineOpenWorkbook", "CalamineWorkbookReader"]
