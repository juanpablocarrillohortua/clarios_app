"""The checks an uploaded file passes before it may land in Bronze.

Layer: domain

Responsibility:
    Applies a dataset spec to the rows of one sheet and says what is wrong,
    with the row, the column and the rule of every problem. Three groups of
    rules run, each with a stable ID that the web app turns into a message
    (``loads.rules.<id>`` in its translation file):

    * **file** - the archive can be read and is not a zip bomb
      (:mod:`energy.domain.workbook_limits`); the spec's sheet exists; its
      header row holds something; rows follow it;
    * **schema** - every column of the spec is there, exactly as named. A
      header that equals the spec's only once case, spaces and accents are
      ignored is an error, because Bronze keys the column by the text found
      and Silver would not find it. A column the spec lacks is reported for
      information;
    * **cell** - for every row: a required value is there; a value has its
      column's type; it matches the pattern, is one of the allowed values and
      lies within the range.

    The checks read the rows exactly as Bronze would keep them
    (:func:`energy.domain.landing.sheet_payloads`) and judge the payload text
    the way the Silver build reads it: a value is trimmed of spaces at both
    ends (``pg_temp.eq_txt``), and a number is what ``pg_temp.eq_num``
    accepts. A date column takes real date cells only, as the spec format
    says. The rows checked are handed back as landing rows, so what is staged
    is what Bronze will land.

    A file broken on every row must not fill the memory or the issues list:
    :class:`IssueCollector` keeps the first issues of each rule in each column
    and counts every affected row exactly.

    The module is pure.

Depends on:
    ``energy.domain.dataset_spec``, ``energy.domain.landing``,
    ``energy.domain.drafts`` (constants only), standard library only.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from itertools import chain
from typing import TYPE_CHECKING, Final

from energy.domain.dataset_spec import ColumnType
from energy.domain.landing import (
    LandingRow,
    canonical_json,
    cell_text,
    column_letter,
    header_keys,
    sha256_hex,
    sheet_payloads,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

    from energy.domain.dataset_spec import ColumnSpec, DatasetSpec
    from energy.domain.landing import CellValue, SheetRow

#: The number the Silver build accepts (``pg_temp.eq_num`` in
#: ``energy.adapters.equs_silver_sql``). A unit test keeps the two equal.
SILVER_NUMBER: Final = r"^[-+]?([0-9]+[.]?[0-9]*|[.][0-9]+)$"
#: How much of a value an issue keeps.
VALUE_SHOWN_LENGTH: Final = 200
#: How many issues of one rule are listed for one column. The counts are exact.
DEFAULT_LISTED_PER_COLUMN: Final = 1000
#: How many sheet names a missing-sheet issue lists.
_SHEETS_LISTED: Final = 20

_NUMBER: Final = re.compile(SILVER_NUMBER)

type ParamValue = str | int | tuple[str, ...]


class IssueSeverity(StrEnum):
    """What an issue does: an error blocks the load, a warning must be accepted."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class RuleId(StrEnum):
    """Every rule a file is checked against, by its stable ID."""

    FILE_UNREADABLE = "file.unreadable"
    FILE_TOO_MANY_PARTS = "file.too_many_parts"
    FILE_CONTENT_TOO_LARGE = "file.content_too_large"
    FILE_SHARED_STRINGS_TOO_LARGE = "file.shared_strings_too_large"
    FILE_SHEET_MISSING = "file.sheet_missing"
    FILE_HEADER_ROW_EMPTY = "file.header_row_empty"
    FILE_NO_ROWS = "file.no_rows"
    SCHEMA_HEADER_MISMATCH = "schema.header_mismatch"
    SCHEMA_COLUMN_MISSING = "schema.column_missing"
    SCHEMA_COLUMN_UNKNOWN = "schema.column_unknown"
    CELL_REQUIRED = "cell.required"
    CELL_TYPE = "cell.type"
    CELL_PATTERN = "cell.pattern"
    CELL_ALLOWED = "cell.allowed"
    CELL_RANGE = "cell.range"


@dataclass(frozen=True, slots=True)
class Issue:
    """One problem found in a file.

    Attributes:
        rule: the rule broken.
        severity: what it does to the load.
        source_row: the Excel row, 1-based; ``None`` for the file as a whole.
        column_index: the 0-based Excel column (``0`` is A); ``None`` when
            the problem is not in one column of the file.
        column_key: the payload key of the spec's column, or of the file's
            column the spec does not know; ``None`` for the file as a whole.
        value: the text found, at most :data:`VALUE_SHOWN_LENGTH` characters.
        params: what the message needs: the pattern, the allowed values ...
    """

    rule: RuleId
    severity: IssueSeverity
    source_row: int | None
    column_index: int | None
    column_key: str | None
    value: str | None
    params: Mapping[str, ParamValue]

    @property
    def column_letter(self) -> str | None:
        """Return the Excel letter of the column, if the issue has one."""
        return column_letter(self.column_index) if self.column_index is not None else None


@dataclass(frozen=True, slots=True)
class RuleCount:
    """How often one rule was broken in one column.

    Attributes:
        rule: the rule.
        severity: what breaking it does.
        column_key: the column; ``None`` for the file as a whole.
        rows: every row that broke it; one for a file or header problem.
        listed: how many of those issues are kept to be listed.
    """

    rule: RuleId
    severity: IssueSeverity
    column_key: str | None
    rows: int
    listed: int


class IssueCollector:
    """Gather the issues of one file without holding them all.

    The first issues of each rule in each column are kept to be listed; every
    one is counted. Kept issues wait in :meth:`take` until the caller stores
    them, so the caller decides how often to write.

    Args:
        listed_per_column: how many issues of one rule are kept per column.
    """

    def __init__(self, listed_per_column: int = DEFAULT_LISTED_PER_COLUMN) -> None:
        self._listed_per_column = listed_per_column
        self._tally: dict[tuple[RuleId, str | None], list[int]] = {}
        self._severity: dict[tuple[RuleId, str | None], IssueSeverity] = {}
        self._pending: list[Issue] = []
        self.rows_with_errors = 0
        self.rows_with_warnings = 0

    def add(self, issue: Issue) -> None:
        """Count one issue, and keep it if its rule and column are not full."""
        key = (issue.rule, issue.column_key)
        tally = self._tally.get(key)
        if tally is None:
            tally = self._tally[key] = [0, 0]
            self._severity[key] = issue.severity
        tally[0] += 1
        if tally[1] < self._listed_per_column:
            tally[1] += 1
            self._pending.append(issue)

    def add_row(self, issues: Sequence[Issue]) -> None:
        """Count the issues of one data row, and the row itself by severity."""
        if not issues:
            return
        for issue in issues:
            self.add(issue)
        severities = {issue.severity for issue in issues}
        if IssueSeverity.ERROR in severities:
            self.rows_with_errors += 1
        if IssueSeverity.WARNING in severities:
            self.rows_with_warnings += 1

    @property
    def pending(self) -> int:
        """Return how many kept issues wait to be stored."""
        return len(self._pending)

    def take(self) -> list[Issue]:
        """Return the kept issues not taken yet, and forget them."""
        taken, self._pending = self._pending, []
        return taken

    def counts(self) -> tuple[RuleCount, ...]:
        """Return how often each rule was broken in each column."""
        return tuple(
            RuleCount(rule, self._severity[(rule, column)], column, rows, listed)
            for (rule, column), (rows, listed) in sorted(
                self._tally.items(), key=lambda item: (item[0][0], item[0][1] or "")
            )
        )


# -----------------------------------------------------------------------------
# File
# -----------------------------------------------------------------------------
def sheet_issue(spec: DatasetSpec, sheet_names: Sequence[str]) -> Issue | None:
    """Return the issue of a workbook that lacks the spec's sheet, or ``None``."""
    if spec.bronze.sheet in sheet_names:
        return None
    return Issue(
        RuleId.FILE_SHEET_MISSING,
        IssueSeverity.ERROR,
        None,
        None,
        None,
        None,
        {"sheet": spec.bronze.sheet, "sheets": tuple(sheet_names[:_SHEETS_LISTED])},
    )


# -----------------------------------------------------------------------------
# Schema
# -----------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Column:
    """A column of the spec, found in the file.

    Attributes:
        spec: the column's spec.
        offset: its position in the file's rows.
        file_key: the key Bronze gives it in this file.
        index: its 0-based Excel column.
        pattern: the compiled pattern, if any.
        minimum: the range's lower bound, if any.
        maximum: the range's upper bound, if any.
        severity: what breaking its pattern, list or range does.
    """

    spec: ColumnSpec
    offset: int
    file_key: str
    index: int
    pattern: re.Pattern[str] | None
    minimum: Decimal | None
    maximum: Decimal | None
    severity: IssueSeverity

    def issue(
        self,
        rule: RuleId,
        severity: IssueSeverity,
        row: int,
        value: str | None,
        **params: ParamValue,
    ) -> Issue:
        """Return an issue of this column."""
        shown = value[:VALUE_SHOWN_LENGTH] if value is not None else None
        return Issue(rule, severity, row, self.index, self.spec.key, shown, params)


def _column(spec: ColumnSpec, offset: int, file_key: str, first_column: int) -> _Column:
    """Return a column of the spec, as found at ``offset`` of the file's rows."""
    return _Column(
        spec=spec,
        offset=offset,
        file_key=file_key,
        index=first_column + offset,
        pattern=re.compile(spec.pattern) if spec.pattern else None,
        minimum=Decimal(str(spec.minimum)) if spec.minimum is not None else None,
        maximum=Decimal(str(spec.maximum)) if spec.maximum is not None else None,
        severity=IssueSeverity(spec.severity.value),
    )


def loose(text: str) -> str:
    """Return text as compared ignoring case, spaces and accents."""
    decomposed = unicodedata.normalize("NFKD", text)
    bare = "".join(char for char in decomposed if not unicodedata.combining(char))
    return "".join(bare.casefold().split())


def _is_blank(cell: CellValue) -> bool:
    """Return whether a header cell holds nothing but spaces."""
    text = cell_text(cell)
    return text is None or not text.strip()


def _match_header(
    spec: DatasetSpec,
    header: Sequence[CellValue],
    first_column: int,
    collector: IssueCollector,
) -> tuple[list[_Column], set[str]]:
    """Find the spec's columns in the header row, reporting what does not match.

    Returns:
        The spec's columns found, and the keys of the file's columns already
        reported as unknown.
    """
    row = spec.bronze.header_row
    found = header_keys(header, first_column)
    blank = {offset for offset, cell in enumerate(header) if _is_blank(cell)}
    by_key = {key: offset for offset, key in enumerate(found) if offset not in blank}
    taken: set[int] = set()
    columns: list[_Column] = []
    unmatched: list[ColumnSpec] = []
    for spec_column in spec.columns:
        offset = by_key.get(spec_column.key)
        if offset is None:
            unmatched.append(spec_column)
            continue
        taken.add(offset)
        columns.append(_column(spec_column, offset, spec_column.key, first_column))

    candidates: dict[str, list[int]] = {}
    for offset, key in enumerate(found):
        if offset not in taken and offset not in blank:
            candidates.setdefault(loose(key), []).append(offset)
    for spec_column in unmatched:
        offsets = candidates.get(loose(spec_column.key))
        if offsets:
            offset = offsets.pop(0)
            taken.add(offset)
            columns.append(_column(spec_column, offset, found[offset], first_column))
            collector.add(
                Issue(
                    RuleId.SCHEMA_HEADER_MISMATCH,
                    IssueSeverity.ERROR,
                    row,
                    first_column + offset,
                    spec_column.key,
                    found[offset],
                    {"found": found[offset], "expected": spec_column.header},
                )
            )
            continue
        collector.add(
            Issue(
                RuleId.SCHEMA_COLUMN_MISSING,
                IssueSeverity.ERROR if spec_column.required else IssueSeverity.WARNING,
                row,
                None,
                spec_column.key,
                None,
                {"header": spec_column.header},
            )
        )

    reported: set[str] = set()
    for offset, key in enumerate(found):
        if offset not in taken and offset not in blank:
            collector.add(_unknown(row, first_column + offset, key))
            reported.add(key)
    return columns, reported


def _unknown(row: int, index: int, key: str) -> Issue:
    """Return the issue of a column the spec does not know."""
    return Issue(
        RuleId.SCHEMA_COLUMN_UNKNOWN, IssueSeverity.INFO, row, index, key, key, {"header": key}
    )


# -----------------------------------------------------------------------------
# Cells
# -----------------------------------------------------------------------------
def _has_type(column_type: ColumnType, text: str, cell: CellValue) -> bool:
    """Return whether a non-empty value is of its column's type."""
    if column_type is ColumnType.TEXT:
        return True
    if column_type is ColumnType.DATE:
        # datetime is a date too; a date typed as text is not a date cell.
        return isinstance(cell, date)
    if not _NUMBER.fullmatch(text):
        return False
    return column_type is ColumnType.DECIMAL or Decimal(text) % 1 == 0


type _Broken = tuple[RuleId, IssueSeverity, dict[str, ParamValue]]


def _range_rule(column: _Column, value: str) -> _Broken | None:
    """Return the range rule a number breaks, or ``None``."""
    if column.minimum is None and column.maximum is None:
        return None
    number = Decimal(value)
    too_low = column.minimum is not None and number < column.minimum
    too_high = column.maximum is not None and number > column.maximum
    if not (too_low or too_high):
        return None
    bounds: dict[str, ParamValue] = {}
    if column.minimum is not None:
        bounds["min"] = str(column.minimum)
    if column.maximum is not None:
        bounds["max"] = str(column.maximum)
    return RuleId.CELL_RANGE, column.severity, bounds


def _broken_rule(column: _Column, value: str, cell: CellValue) -> _Broken | None:
    """Return the first rule a non-empty value breaks, or ``None``."""
    spec = column.spec
    if not _has_type(spec.type, value, cell):
        return RuleId.CELL_TYPE, IssueSeverity.ERROR, {"type": spec.type.value}
    if column.pattern is not None and not column.pattern.fullmatch(value):
        return RuleId.CELL_PATTERN, column.severity, {"pattern": spec.pattern or ""}
    if spec.allowed is not None and value not in spec.allowed:
        return RuleId.CELL_ALLOWED, column.severity, {"allowed": spec.allowed}
    return _range_rule(column, value)


def _check_cell(column: _Column, kept: SheetRow) -> Issue | None:
    """Return the first rule one cell breaks, or ``None``."""
    text = kept.payload.get(column.file_key)
    value = text.strip(" ") if text else ""
    if not value:
        if column.spec.required:
            return column.issue(RuleId.CELL_REQUIRED, IssueSeverity.ERROR, kept.source_row, text)
        return None
    cell = kept.cells[column.offset] if column.offset < len(kept.cells) else None
    broken = _broken_rule(column, value, cell)
    if broken is None:
        return None
    rule, severity, params = broken
    return column.issue(rule, severity, kept.source_row, text, **params)


def _check_row(columns: Sequence[_Column], kept: SheetRow) -> list[Issue]:
    """Return what one row breaks: at most one issue per column."""
    issues: list[Issue] = []
    for column in columns:
        issue = _check_cell(column, kept)
        if issue is not None:
            issues.append(issue)
    return issues


# -----------------------------------------------------------------------------
# A sheet
# -----------------------------------------------------------------------------
def check_sheet(
    spec: DatasetSpec,
    rows: Iterable[Sequence[CellValue]],
    collector: IssueCollector,
    first_row: int = 1,
    first_column: int = 0,
) -> Iterator[LandingRow]:
    """Check the rows of the spec's sheet, yielding them as Bronze would land them.

    Every problem goes to ``collector``. A header row that holds nothing stops
    the check: no column can be found. Rows are yielded whatever they break,
    so the caller can stage them all.

    Args:
        spec: the dataset spec.
        rows: the rows of the sheet's used range, top to bottom.
        collector: where the issues go.
        first_row: Excel row number of the first row in ``rows``.
        first_column: 0-based index of the first column of every row.

    Yields:
        One landing row per kept row, in sheet order.
    """
    header_row = spec.bronze.header_row
    remaining = iter(rows)
    header: Sequence[CellValue] | None = None
    for number, row in enumerate(remaining, start=first_row):
        if number == header_row:
            header = row
            break
    if header is None or all(_is_blank(cell) for cell in header):
        collector.add(
            Issue(
                RuleId.FILE_HEADER_ROW_EMPTY,
                IssueSeverity.ERROR,
                header_row,
                None,
                None,
                None,
                {"row": header_row},
            )
        )
        return

    columns, known = _match_header(spec, header, first_column, collector)
    known.update(column.file_key for column in columns)
    rows_read = 0
    for kept in sheet_payloads(chain([header], remaining), header_row, first_column, header_row):
        rows_read += 1
        collector.add_row(_check_row(columns, kept))
        for key in kept.payload:
            if key not in known:
                # A column with data and no header, or past the header's end.
                known.add(key)
                collector.add(_unknown(header_row, _index_of(key, kept, first_column), key))
        payload_json = canonical_json(kept.payload)
        yield LandingRow(kept.source_row, sha256_hex(payload_json.encode("utf-8")), payload_json)
    if not rows_read:
        collector.add(
            Issue(
                RuleId.FILE_NO_ROWS,
                IssueSeverity.ERROR,
                None,
                None,
                None,
                None,
                {"sheet": spec.bronze.sheet},
            )
        )


def _index_of(key: str, kept: SheetRow, first_column: int) -> int:
    """Return the Excel column of a payload key of one row."""
    if key in kept.keys:
        return first_column + kept.keys.index(key)
    for offset in range(len(kept.keys), len(kept.cells)):
        if column_letter(first_column + offset) == key:
            return first_column + offset
    return first_column


__all__ = [
    "DEFAULT_LISTED_PER_COLUMN",
    "SILVER_NUMBER",
    "VALUE_SHOWN_LENGTH",
    "Issue",
    "IssueCollector",
    "IssueSeverity",
    "ParamValue",
    "RuleCount",
    "RuleId",
    "check_sheet",
    "loose",
    "sheet_issue",
]
