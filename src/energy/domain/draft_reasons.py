"""Why a problem of an uploaded file happened, and how to fix it in Excel.

Layer: domain

Responsibility:
    Gives every issue :mod:`energy.domain.draft_rules` finds its likely
    reasons, most specific first. Each reason is an ID and a few values; the
    web app turns it into two sentences, why it happened and what to do in
    Excel (``loads.reasons.<id>.why`` and ``.fix`` in its translation file).
    No sentence lives in Python.

    The detectors are small, deterministic functions of what the checks
    already hold: the text Bronze would store, the type of the cell as the
    workbook kept it, the column's spec, the row around it and the headers. No
    model is involved: the same file always gets the same reasons, and each
    detector has a test.

    Every issue ends with its rule's own reason (``default.<rule id>``), so no
    issue is ever left without one. A detector that cannot be sure says
    "probably" in its sentence (``header.similar``, ``sheet.near_match``).
    Reasons that need data outside the file (an item known under another
    code, a key loaded before with other values) wait for increment 8.

    The module is pure.

Depends on:
    standard library only.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

type ReasonParam = str | int | tuple[str, ...]


class ReasonId(StrEnum):
    """Every reason an issue can be given, by its stable ID."""

    # A whole row.
    ROW_HEADER_REPEATED = "row.header_repeated"
    # Dates.
    DATE_TYPED_AS_TEXT = "date.typed_as_text"
    DATE_LOST_FORMAT = "date.lost_format"
    # Numbers.
    NUMBER_TEXT_WITH_SEPARATORS = "number.text_with_separators"
    # Codes Excel turned into numbers.
    CODE_SCIENTIFIC_NOTATION = "code.scientific_notation"
    CODE_TRAILING_ZERO_DECIMAL = "code.trailing_zero_decimal"
    CODE_LOST_LEADING_ZEROS = "code.lost_leading_zeros"
    # Text.
    TEXT_LOWERCASE = "text.lowercase"
    TEXT_INVISIBLE_CHARACTERS = "text.invisible_characters"
    TEXT_WRONG_ENCODING = "text.wrong_encoding"
    ALLOWED_NEAR_MATCH = "allowed.near_match"
    # Headers and sheets.
    HEADER_NEAR_MATCH = "header.near_match"
    HEADER_SIMILAR = "header.similar"
    SHEET_NEAR_MATCH = "sheet.near_match"
    # Why a file cannot be read.
    UNREADABLE_MACROS = "unreadable.macros"
    UNREADABLE_CORRUPT = "unreadable.corrupt"
    UNREADABLE_NOT_A_SPREADSHEET = "unreadable.not_a_spreadsheet"
    UNREADABLE_NOT_A_ZIP = "unreadable.not_a_zip"
    # Each rule's own reason: the last of every issue.
    DEFAULT_FILE_UNREADABLE = "default.file.unreadable"
    DEFAULT_FILE_TOO_MANY_PARTS = "default.file.too_many_parts"
    DEFAULT_FILE_CONTENT_TOO_LARGE = "default.file.content_too_large"
    DEFAULT_FILE_SHARED_STRINGS_TOO_LARGE = "default.file.shared_strings_too_large"
    DEFAULT_FILE_SHEET_MISSING = "default.file.sheet_missing"
    DEFAULT_FILE_HEADER_ROW_EMPTY = "default.file.header_row_empty"
    DEFAULT_FILE_NO_ROWS = "default.file.no_rows"
    DEFAULT_SCHEMA_HEADER_MISMATCH = "default.schema.header_mismatch"
    DEFAULT_SCHEMA_COLUMN_MISSING = "default.schema.column_missing"
    DEFAULT_SCHEMA_COLUMN_UNKNOWN = "default.schema.column_unknown"
    DEFAULT_CELL_REQUIRED = "default.cell.required"
    DEFAULT_CELL_TYPE = "default.cell.type"
    DEFAULT_CELL_PATTERN = "default.cell.pattern"
    DEFAULT_CELL_ALLOWED = "default.cell.allowed"
    DEFAULT_CELL_RANGE = "default.cell.range"
    DEFAULT_CELL_TEXT_DAMAGED = "default.cell.text_damaged"


@dataclass(frozen=True, slots=True)
class Reason:
    """One likely reason for an issue.

    Attributes:
        id: which reason.
        params: the values its sentences need: the date a number stands for,
            the allowed value meant, the characters found ...
    """

    id: ReasonId
    params: Mapping[str, ReasonParam] = field(default_factory=dict)


#: Excel's day numbers: 1 is 1 January 1900, 2,958,465 is 31 December 9999.
EXCEL_LAST_SERIAL: Final = 2_958_465
#: Excel counts 29 February 1900, a day that never was, so from day 61 on a
#: day number is one more than the days since 31 December 1899.
_EXCEL_EPOCH: Final = date(1899, 12, 30)
_EXCEL_LEAP_BUG_FROM: Final = 61

#: How far apart two names may be, in edits, to be called "probably the same".
NEAR_NAME_EDITS: Final = 2
#: A name shorter than this is never matched by edits: too many names are
#: two edits from a short one.
_NEAR_NAME_SHORTEST: Final = 4
#: The share of a row's filled cells that must repeat their headers.
_HEADER_ROW_SHARE: Final = 0.5
_HEADER_ROW_FEWEST: Final = 2

_DATE_TEXT: Final = re.compile(
    r"^(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}[/.-]\d{1,2}[/.-]\d{1,2})"
    r"([ T]\d{1,2}:\d{2}(:\d{2})?)?$"
)
_NUMBER_WITH_SEPARATORS: Final = re.compile(
    r"^[-+]?(\d{1,3}(\.\d{3})+(,\d+)?"  # 1.234,56 or 1.234.567
    r"|\d{1,3}(,\d{3})+(\.\d+)?"  # 1,234.56 or 1,234,567
    r"|\d{1,3}( \d{3})+(,\d+)?"  # 1 234,56
    r"|\d+,\d+)$"  # 12,5
)
_SCIENTIFIC: Final = re.compile(r"^[-+]?\d+([.,]\d+)?[eE][+-]?\d+$")
_TRAILING_ZERO_DECIMAL: Final = re.compile(r"^\d+[.,]0+$")
_FIXED_DIGITS: Final = re.compile(r"^\^(?:\[0-9\]|\\d)\{(\d+)\}\$$")
_WRONG_ENCODING: Final = re.compile(r"[ÃÂ][^\x00-\x7f]|â€")

#: Characters a copy and paste leaves behind, by the name the app gives them.
_INVISIBLE: Final[dict[str, str]] = {
    "\u00a0": "nbsp",
    "\u2007": "nbsp",
    "\u202f": "nbsp",
    "\t": "tab",
    "\u200b": "zero_width",
    "\u200c": "zero_width",
    "\u200d": "zero_width",
    "\u2060": "zero_width",
    "\ufeff": "zero_width",
    "\n": "line_break",
    "\r": "line_break",
}
_LINE_BREAK: Final = "line_break"

#: The rule each default reason belongs to, by the rule's ID.
_DEFAULTS: Final[dict[str, ReasonId]] = {
    reason.value.removeprefix("default."): reason
    for reason in ReasonId
    if reason.value.startswith("default.")
}
_UNREADABLE: Final[dict[str, ReasonId]] = {
    "macros": ReasonId.UNREADABLE_MACROS,
    "corrupt": ReasonId.UNREADABLE_CORRUPT,
    "not_a_spreadsheet": ReasonId.UNREADABLE_NOT_A_SPREADSHEET,
    "not_a_zip": ReasonId.UNREADABLE_NOT_A_ZIP,
}


# -----------------------------------------------------------------------------
# Comparing names
# -----------------------------------------------------------------------------
def _without_accents(text: str) -> str:
    """Return text with its accents taken off its letters."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def loose(text: str) -> str:
    """Return text as compared ignoring case, spaces and accents.

    Python counts the non-breaking space and its kind as spaces, so they go
    too: a value that differs only by them is the same value.
    """
    return "".join(_without_accents(text).casefold().split())


def edits_between(first: str, second: str) -> int:
    """Return how many single-character edits turn one text into the other."""
    if len(first) < len(second):
        first, second = second, first
    previous = list(range(len(second) + 1))
    for i, left in enumerate(first, start=1):
        current = [i]
        for j, right in enumerate(second, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (left != right))
            )
        previous = current
    return previous[-1]


def _near(found: str, expected: str) -> bool:
    """Return whether two names are probably meant to be the same."""
    a, b = loose(found), loose(expected)
    if a == b:
        return True
    return min(len(a), len(b)) >= _NEAR_NAME_SHORTEST and edits_between(a, b) <= NEAR_NAME_EDITS


def header_difference(found: str, expected: str) -> str:
    """Name how a header differs from the spec's: case, accent, spaces or several."""
    if found.casefold() == expected.casefold():
        return "case"
    if _without_accents(found) == _without_accents(expected):
        return "accent"
    if " ".join(found.split()) == " ".join(expected.split()) or "".join(found.split()) == "".join(
        expected.split()
    ):
        return "spaces"
    return "several"


# -----------------------------------------------------------------------------
# Single detectors
# -----------------------------------------------------------------------------
def excel_serial_date(serial: float) -> date | None:
    """Return the date an Excel day number stands for, or ``None`` if it is none.

    Workbooks in Excel's 1904 date system (old Mac) count from another day;
    this reads the 1900 system, which is the one Windows writes.
    """
    if not 1 <= serial <= EXCEL_LAST_SERIAL:
        return None
    whole = int(serial)
    if whole < _EXCEL_LEAP_BUG_FROM:
        return _EXCEL_EPOCH + timedelta(days=whole + 1)
    return _EXCEL_EPOCH + timedelta(days=whole)


def invisible_characters(text: str, *, line_breaks: bool = True) -> tuple[str, ...]:
    """Return the names of the invisible characters in text, in a fixed order.

    Args:
        text: the value.
        line_breaks: whether a line break counts. In free text it is often
            meant (Alt+Enter in Excel); in a code or a number it never is.
    """
    found = {_INVISIBLE[char] for char in text if char in _INVISIBLE}
    found.update(
        "control"
        for char in text
        if char not in _INVISIBLE and unicodedata.category(char) in {"Cc", "Cf"}
    )
    if not line_breaks:
        found.discard(_LINE_BREAK)
    return tuple(sorted(found))


def repaired_encoding(text: str) -> str | None:
    """Return text read back as UTF-8, if it is UTF-8 that was read as Latin-1.

    ``DescripciÃ³n`` gives ``Descripción``. Text that shows no sign of it, or
    that cannot be read back, gives ``None``.
    """
    if not _WRONG_ENCODING.search(text):
        return None
    for codec in ("cp1252", "latin-1"):
        try:
            repaired = text.encode(codec).decode("utf-8")
        except UnicodeError:
            continue
        if repaired != text:
            return repaired
    return None


def _encoding_reason(text: str) -> Reason | None:
    """Return the wrong-encoding reason of a text, if it has the marks of one."""
    if not _WRONG_ENCODING.search(text):
        return None
    repaired = repaired_encoding(text)
    return Reason(ReasonId.TEXT_WRONG_ENCODING, {"repaired": repaired} if repaired else {})


def _invisible_reason(text: str, *, line_breaks: bool = True) -> Reason | None:
    """Return the invisible-characters reason of a text, if it holds any."""
    found = invisible_characters(text, line_breaks=line_breaks)
    return Reason(ReasonId.TEXT_INVISIBLE_CHARACTERS, {"characters": found}) if found else None


def _code_as_number_reason(text: str) -> Reason | None:
    """Return the reason of a code Excel wrote as a number, from its text."""
    if _SCIENTIFIC.fullmatch(text):
        return Reason(ReasonId.CODE_SCIENTIFIC_NOTATION)
    if _TRAILING_ZERO_DECIMAL.fullmatch(text):
        return Reason(ReasonId.CODE_TRAILING_ZERO_DECIMAL)
    return None


def text_damage(text: str) -> Reason | None:
    """Return what damaged a free-text value, or ``None`` if it looks sound.

    The checks of rule ``cell.text_damaged``: a code Excel wrote as a number,
    characters a paste left behind (a line break is allowed in free text),
    or text read in the wrong encoding.
    """
    return (
        _code_as_number_reason(text)
        or _invisible_reason(text, line_breaks=False)
        or _encoding_reason(text)
    )


def repeats_headers(cells: Iterable[tuple[str | None, str]]) -> bool:
    """Return whether a data row is a copy of the header row.

    Args:
        cells: each column's value in the row and the column's header.

    Returns:
        ``True`` when at least two filled cells, and at least half of them,
        equal their headers once case, spaces and accents are ignored.
    """
    filled = 0
    repeated = 0
    for value, header in cells:
        if value is None or not value.strip():
            continue
        filled += 1
        repeated += loose(value) == loose(header)
    return repeated >= _HEADER_ROW_FEWEST and repeated >= filled * _HEADER_ROW_SHARE


# -----------------------------------------------------------------------------
# The reasons of one issue
# -----------------------------------------------------------------------------
def _date_reasons(text: str, cell: object) -> list[Reason]:
    """Return why a date column holds no date."""
    if isinstance(cell, bool):
        return []
    if isinstance(cell, int | float):
        stands_for = excel_serial_date(cell)
        if stands_for is not None:
            return [Reason(ReasonId.DATE_LOST_FORMAT, {"date": stands_for.isoformat()})]
        return []
    if isinstance(cell, str) and _DATE_TEXT.fullmatch(text.strip()):
        return [Reason(ReasonId.DATE_TYPED_AS_TEXT)]
    return []


def cell_reasons(
    rule: str,
    column_type: str,
    text: str,
    cell: object,
    pattern: str | None = None,
    allowed: Sequence[str] | None = None,
) -> list[Reason]:
    """Return the specific reasons of a cell's issue, most specific first.

    Args:
        rule: the ID of the rule the cell broke.
        column_type: the column's type in the spec (``text``, ``date`` ...).
        text: the text Bronze would store, untrimmed.
        cell: the value as the workbook kept it: text, a number, a date.
        pattern: the column's pattern, if any.
        allowed: the column's allowed values, if any.

    Returns:
        The reasons, without the rule's default; possibly none.
    """
    value = text.strip(" ")
    reasons: list[Reason | None] = []
    if rule == "cell.type" and column_type == "date":
        reasons.extend(_date_reasons(value, cell))
    elif rule == "cell.type" and _NUMBER_WITH_SEPARATORS.fullmatch(value):
        reasons.append(Reason(ReasonId.NUMBER_TEXT_WITH_SEPARATORS))
    if rule == "cell.pattern":
        reasons.append(_pattern_reason(value, cell, pattern))
    if rule == "cell.allowed" and allowed:
        meant = next((option for option in allowed if loose(option) == loose(value)), None)
        if meant is not None:
            reasons.append(Reason(ReasonId.ALLOWED_NEAR_MATCH, {"suggestion": meant}))
    if rule == "cell.text_damaged":
        reasons.append(text_damage(text))
    elif rule in {"cell.type", "cell.pattern", "cell.allowed"}:
        reasons.append(_invisible_reason(text))
        reasons.append(_encoding_reason(value))
    return _unique(reasons)


def _pattern_reason(value: str, cell: object, pattern: str | None) -> Reason | None:
    """Return why a value misses its column's pattern, when it can be told."""
    fixed = _FIXED_DIGITS.fullmatch(pattern or "")
    if fixed and isinstance(cell, int | float) and not isinstance(cell, bool):
        digits = int(fixed.group(1))
        if value.isdigit() and len(value) < digits:
            return Reason(ReasonId.CODE_LOST_LEADING_ZEROS, {"digits": digits})
    as_number = _code_as_number_reason(value)
    if as_number is not None:
        return as_number
    if pattern and value != value.upper() and re.fullmatch(pattern, value.upper()):
        return Reason(ReasonId.TEXT_LOWERCASE)
    return None


def header_reasons(
    rule: str,
    expected: str | None,
    found: str | None,
    unknown_headers: Sequence[str] = (),
) -> list[Reason]:
    """Return the specific reasons of a header's issue.

    Args:
        rule: the ID of the rule.
        expected: the spec's header, for a mismatched or missing column.
        found: the file's header, for a mismatched or unknown column.
        unknown_headers: the file's headers the spec does not know, to find
            the one a missing column probably is.
    """
    reasons: list[Reason | None] = []
    if rule == "schema.header_mismatch" and expected is not None and found is not None:
        difference = header_difference(found, expected)
        reasons.append(Reason(ReasonId.HEADER_NEAR_MATCH, {"difference": difference}))
    if rule == "schema.column_missing" and expected is not None:
        for candidate in unknown_headers:
            repaired = repaired_encoding(candidate)
            if repaired is not None and loose(repaired) == loose(expected):
                reasons.append(
                    Reason(ReasonId.TEXT_WRONG_ENCODING, {"repaired": repaired, "found": candidate})
                )
                break
            if _near(candidate, expected):
                reasons.append(Reason(ReasonId.HEADER_SIMILAR, {"found": candidate}))
                break
    if rule == "schema.column_unknown" and found is not None:
        reasons.append(_encoding_reason(found))
    return _unique(reasons)


def sheet_reasons(expected: str, sheets: Sequence[str]) -> list[Reason]:
    """Return the sheet a missing sheet probably is, if one is close enough."""
    for name in sheets:
        if _near(name, expected):
            return [Reason(ReasonId.SHEET_NEAR_MATCH, {"found": name})]
    return []


def default_reason(rule: str, params: Mapping[str, object] | None = None) -> Reason:
    """Return a rule's own reason: the last, and sometimes the only, of an issue.

    A file that cannot be read gets the reason its check named (macros, a
    damaged file ...).

    Raises:
        KeyError: for a rule this module does not know; a unit test keeps
            every rule known.
    """
    if rule == "file.unreadable" and params:
        named = _UNREADABLE.get(str(params.get("reason", "")))
        if named is not None:
            return Reason(named)
    return Reason(_DEFAULTS[rule])


def ranked(
    rule: str,
    params: Mapping[str, object],
    specific: Iterable[Reason],
    row: Iterable[Reason] = (),
) -> tuple[Reason, ...]:
    """Return an issue's reasons in order: the row's, the cell's, then the rule's.

    A reason given twice is kept once, where it first appears.
    """
    return tuple(_unique([*row, *specific, default_reason(rule, params)]))


def _unique(reasons: Iterable[Reason | None]) -> list[Reason]:
    """Return the reasons given, without ``None`` and without repeats, in order."""
    kept: list[Reason] = []
    seen: set[ReasonId] = set()
    for reason in reasons:
        if reason is not None and reason.id not in seen:
            seen.add(reason.id)
            kept.append(reason)
    return kept


__all__ = [
    "EXCEL_LAST_SERIAL",
    "NEAR_NAME_EDITS",
    "Reason",
    "ReasonId",
    "ReasonParam",
    "cell_reasons",
    "default_reason",
    "edits_between",
    "excel_serial_date",
    "header_difference",
    "header_reasons",
    "invisible_characters",
    "loose",
    "ranked",
    "repaired_encoding",
    "repeats_headers",
    "sheet_reasons",
    "text_damage",
]
