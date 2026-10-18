"""Rules that turn a spreadsheet row into a Bronze landing row.

Layer: domain

Responsibility:
    Defines, in one place, the contract of a Bronze landing row for the
    equivalent-batteries (EQUS) sources: how a column gets its key, how a cell
    becomes text, which rows are kept, how a payload is fingerprinted, and which
    sheets each source key may land. Bronze stores what arrived, so every rule
    here is about *preserving* a row, never about interpreting it: nothing is
    parsed into a typed value, nothing is trimmed except header texts, and
    duplicated rows are kept. Silver reads payloads by these keys, which is why
    the rules live in the domain rather than inside one reader.

    It also notes, for the sources that are dated, the first and the last date
    a load brought, so a run can say which period it covers without anyone
    telling it. That reads the date cells as the workbook holds them and changes
    no payload.

    The module is pure. It receives cell values a reader has already extracted
    and knows nothing about workbooks, files or databases.

Depends on:
    ``energy.domain.exceptions``, standard library only.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, NamedTuple

from energy.domain.exceptions import ConfigurationError, UnknownSourceError

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence

#: What a workbook reader may hand over for one cell. ``None`` and the empty
#: string both mean "empty cell".
type CellValue = str | int | float | bool | date | datetime | time | timedelta | None

DEFAULT_PLANT_CODE: Final = "1515"

SOURCE_SYSTEM_QAD: Final = "qad"
SOURCE_SYSTEM_EXCEL: Final = "excel"

# The accounting date of a movement. It is the column that says which period an
# export covers, whatever years that turns out to be.
PERIOD_KEY: Final = "Fecha Efectiva"

_ALPHABET_SIZE: Final = 26
_DATASET_KEY: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_SECONDS_PER_MINUTE: Final = 60
_SECONDS_PER_HOUR: Final = 3600

# Sheets of the EQUS BATT workbook that hold Excel-calculated columns. Their
# values are outputs of the calculation being replaced, so landing them would
# feed the result back in as if it were a source.
FORBIDDEN_SHEETS: Final = frozenset({"reporte qad", "unit_ship"})


# -----------------------------------------------------------------------------
# Sheets and sources
# -----------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SheetSpec:
    """One worksheet to land.

    Attributes:
        name: the worksheet name, exactly as the workbook spells it.
        dataset_key: for hand-maintained catalogues, the lowercase snake_case
            name of the catalogue (``clasif_poly``, ``instrucc``); ``None`` for
            every other source.
        header_row: 1-based row holding the headers. ``0`` means the sheet has no
            header row and every key is a column letter.
    """

    name: str
    dataset_key: str | None = None
    header_row: int = 1


@dataclass(frozen=True, slots=True)
class LandingSource:
    """What the loader needs to know about one source key.

    Attributes:
        key: the source key, as constrained by ``bronze.ingestion_runs``.
        source_system: ``qad`` for exports of the ERP, ``excel`` for workbooks
            people maintain by hand.
        default_sheets: the sheets landed when the caller names none. Empty when
            the sheets differ per workbook and must always be given.
        needs_dataset_key: whether every sheet must carry a ``dataset_key``.
        period_key: the payload key of the column that dates each row, for the
            sources that are movements over time; ``None`` for snapshots such as
            product structures and catalogues.
    """

    key: str
    source_system: str
    default_sheets: tuple[SheetSpec, ...]
    needs_dataset_key: bool = False
    period_key: str | None = None


LANDING_SOURCES: Final[Mapping[str, LandingSource]] = MappingProxyType(
    {
        source.key: source
        for source in (
            LandingSource(
                "qad_rct_wo", SOURCE_SYSTEM_QAD, (SheetSpec("Datos"),), period_key=PERIOD_KEY
            ),
            LandingSource(
                "qad_facturas", SOURCE_SYSTEM_QAD, (SheetSpec("Datos"),), period_key=PERIOD_KEY
            ),
            # Product structures come from two workbooks with different sheets.
            LandingSource("qad_product_struc", SOURCE_SYSTEM_QAD, ()),
            LandingSource(
                "item_master",
                SOURCE_SYSTEM_EXCEL,
                (SheetSpec("Datos"), SheetSpec("Grupo-Fam")),
            ),
            LandingSource("manual_sheets", SOURCE_SYSTEM_EXCEL, (), needs_dataset_key=True),
        )
    }
)


def landing_source(key: str) -> LandingSource:
    """Return the landing source for a key.

    Args:
        key: the source key.

    Returns:
        The matching :class:`LandingSource`.

    Raises:
        UnknownSourceError: if no landing source has that key.
    """
    try:
        return LANDING_SOURCES[key]
    except KeyError:
        raise UnknownSourceError(
            "unknown source key", source_key=key, known=sorted(LANDING_SOURCES)
        ) from None


def parse_sheet_spec(text: str, default_header_row: int = 1) -> SheetSpec:
    """Parse a sheet given on the command line.

    The form is ``NAME[=dataset_key][:header_row]``. A colon cannot appear in an
    Excel sheet name, so it always introduces the header row. The part after the
    last ``=`` is a dataset key only when it is lowercase snake_case; otherwise
    the equals sign belongs to the sheet name.

    Args:
        text: for example ``Datos``, ``Clasif_Poly=clasif_poly`` or
            ``INSTRUCC=instrucc:0``.
        default_header_row: the header row when ``text`` names none.

    Returns:
        The parsed :class:`SheetSpec`.

    Raises:
        ConfigurationError: if the sheet name is empty or the header row is not
            a non-negative integer.
    """
    remainder = text.strip()
    header_row = default_header_row

    if ":" in remainder:
        remainder, _, row_text = remainder.rpartition(":")
        if not row_text.strip().isdigit():
            raise ConfigurationError("header row must be a non-negative integer", sheet=text)
        header_row = int(row_text)

    dataset_key: str | None = None
    name, separator, candidate = remainder.rpartition("=")
    if separator and _DATASET_KEY.fullmatch(candidate.strip()):
        remainder, dataset_key = name, candidate.strip()

    if not remainder.strip():
        raise ConfigurationError("sheet name is empty", sheet=text)
    return SheetSpec(remainder.strip(), dataset_key, header_row)


def is_valid_dataset_key(value: str) -> bool:
    """Return whether a dataset key is lowercase snake_case."""
    return bool(_DATASET_KEY.fullmatch(value))


# -----------------------------------------------------------------------------
# Keys
# -----------------------------------------------------------------------------
def column_letter(index: int) -> str:
    """Return the Excel letter of a column.

    Args:
        index: 0-based column index; ``0`` is column A.

    Returns:
        ``A`` to ``Z``, then ``AA``, ``AB`` and so on.

    Raises:
        ValueError: if ``index`` is negative.
    """
    if index < 0:
        msg = f"column index must not be negative, got {index}"
        raise ValueError(msg)
    letters = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, _ALPHABET_SIZE)
        letters = chr(ord("A") + remainder) + letters
    return letters


def header_keys(header: Sequence[CellValue], first_column: int = 0) -> tuple[str, ...]:
    """Build the payload keys of a sheet from its header row.

    A key is the header text, trimmed. A blank header becomes the Excel letter of
    its column, so no value is lost for lack of a name. A repeated header gets a
    suffix - the second ``Descripción`` becomes ``Descripción #2`` - because the
    QAD exports repeat header texts and a JSON object cannot.

    Args:
        header: the cells of the header row.
        first_column: 0-based index of the column the first cell sits in.

    Returns:
        One unique key per cell, in column order.
    """
    keys: list[str] = []
    seen: set[str] = set()
    for offset, cell in enumerate(header):
        text = cell_text(cell)
        base = text.strip() if text is not None else ""
        if not base:
            base = column_letter(first_column + offset)
        key = base
        repeat = 1
        while key in seen:
            repeat += 1
            key = f"{base} #{repeat}"
        seen.add(key)
        keys.append(key)
    return tuple(keys)


def column_keys(width: int, first_column: int = 0) -> tuple[str, ...]:
    """Return column-letter keys for a sheet that has no header row.

    Args:
        width: how many columns the sheet's rows have.
        first_column: 0-based index of the first column.

    Returns:
        ``width`` Excel column letters, starting at ``first_column``.
    """
    return tuple(column_letter(first_column + offset) for offset in range(width))


# -----------------------------------------------------------------------------
# Cells
# -----------------------------------------------------------------------------
def _float_text(value: float) -> str:
    """Render a float as plain decimal text, never in scientific notation."""
    if not math.isfinite(value):
        return repr(value)
    if value.is_integer():
        return str(int(value))
    text = repr(value)
    if "e" in text or "E" in text:
        return format(Decimal(text), "f")
    return text


def _duration_text(value: timedelta) -> str:
    """Render a duration as an ISO-8601 duration."""
    total = value.total_seconds()
    sign = "-" if total < 0 else ""
    remaining = abs(total)
    hours, remaining = divmod(remaining, _SECONDS_PER_HOUR)
    minutes, seconds = divmod(remaining, _SECONDS_PER_MINUTE)
    return f"{sign}PT{int(hours)}H{int(minutes)}M{_float_text(seconds)}S"


def cell_text(value: CellValue) -> str | None:
    """Render one cell as the text Bronze stores.

    The rules keep what the workbook showed without deciding what it means:

    * a string is kept exactly, untrimmed;
    * a whole number has no trailing ``.0`` (``900.0`` becomes ``900``);
    * any other number is its shortest exact decimal form, never scientific
      notation (``1e-05`` becomes ``0.00001``);
    * dates, date-times and times are ISO-8601;
    * booleans are ``true`` and ``false``.

    Args:
        value: the cell value as a workbook reader returned it.

    Returns:
        The text, or ``None`` for an empty cell, which the payload then omits.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    # bool before int, and datetime before date: each is a subclass of the next.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value) if isinstance(value, int) else _float_text(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    return _duration_text(value)


def row_payload(
    keys: Sequence[str], row: Sequence[CellValue], first_column: int = 0
) -> dict[str, str]:
    """Build the payload of one row.

    Args:
        keys: the sheet's keys, from :func:`header_keys` or :func:`column_keys`.
        row: the cells of the row.
        first_column: 0-based index of the column the first cell sits in. Used
            only for cells beyond the last key, which are keyed by column letter.

    Returns:
        Key to cell text for every non-empty cell. Empty for an empty row.
    """
    payload: dict[str, str] = {}
    known = len(keys)
    for offset, value in enumerate(row):
        text = value if type(value) is str else cell_text(value)
        if not text:
            continue
        key = keys[offset] if offset < known else column_letter(first_column + offset)
        payload[key] = text
    return payload


# -----------------------------------------------------------------------------
# Fingerprints
# -----------------------------------------------------------------------------
def sha256_hex(data: bytes) -> str:
    """Return the lowercase SHA-256 hex digest of some bytes."""
    return hashlib.sha256(data).hexdigest()


def canonical_json(payload: Mapping[str, str]) -> str:
    """Serialise a payload so that equal payloads give equal text.

    Keys are sorted, separators are compact and non-ASCII characters are written
    as themselves, so the text - and therefore its hash - does not depend on the
    order the columns were read in.

    Args:
        payload: the row payload.

    Returns:
        The canonical JSON text.
    """
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class LandingRow(NamedTuple):
    """One row ready to land.

    Attributes:
        source_row: the Excel row number, 1-based.
        content_hash: SHA-256 of ``payload_json`` encoded as UTF-8.
        payload_json: the canonical JSON of the payload.
    """

    source_row: int
    content_hash: str
    payload_json: str


class DateSpan:
    """The earliest and the latest date seen in one column of a load.

    Only cells the workbook holds as dates count. A date typed as text is not
    read, because Bronze never parses a value; a column made of such text leaves
    the span empty. The span says what the rows hold, so an odd date in an
    export shows up in it rather than being hidden.

    Args:
        key: the payload key of the column to watch.

    Attributes:
        key: the payload key of the watched column.
        first: the earliest date seen; ``None`` until one is.
        last: the latest date seen; ``None`` until one is.
    """

    __slots__ = ("first", "key", "last")

    def __init__(self, key: str) -> None:
        self.key = key
        self.first: date | None = None
        self.last: date | None = None

    def observe(self, value: CellValue) -> None:
        """Take one cell of the watched column into account.

        Args:
            value: the cell as the workbook reader returned it. Anything that is
                not a date or a date-time is ignored.
        """
        if isinstance(value, datetime):
            day = value.date()
        elif isinstance(value, date):
            day = value
        else:
            return
        if self.first is None or day < self.first:
            self.first = day
        if self.last is None or day > self.last:
            self.last = day

    def bounds(self) -> tuple[date | None, date | None]:
        """Return the earliest and the latest date seen so far."""
        return self.first, self.last


def _watched_column(keys: Sequence[str], span: DateSpan | None) -> int:
    """Return the position of the column a span watches, or ``-1``."""
    if span is None or span.key not in keys:
        return -1
    return keys.index(span.key)


class SheetRow(NamedTuple):
    """One kept row of a sheet, before it is fingerprinted.

    Attributes:
        source_row: the Excel row number, 1-based.
        keys: the sheet's keys, in column order. Every row of a sheet carries
            the same tuple.
        cells: the row's cells as the workbook reader returned them.
        payload: key to cell text for every non-empty cell.
    """

    source_row: int
    keys: tuple[str, ...]
    cells: Sequence[CellValue]
    payload: dict[str, str]


def sheet_payloads(
    rows: Iterable[Sequence[CellValue]],
    first_row: int = 1,
    first_column: int = 0,
    header_row: int = 1,
) -> Iterator[SheetRow]:
    """Turn the rows of a sheet into payloads, keeping the cells beside them.

    This is where the keeping rules live: rows above the header row are not
    data and are skipped, as are rows that are completely empty. Everything
    else is kept, including rows that repeat an earlier one and a header row
    pasted again further down: deciding what those mean belongs to Silver.
    :func:`landing_rows` fingerprints what this yields, and the checks of an
    uploaded file read the same rows, so both see exactly what Bronze stores.

    Args:
        rows: the rows of the sheet's used range, top to bottom.
        first_row: Excel row number of the first row in ``rows``.
        first_column: 0-based index of the first column of every row.
        header_row: 1-based header row; ``0`` when the sheet has none.

    Yields:
        One :class:`SheetRow` per kept row, carrying its real Excel row number.
    """
    keys: tuple[str, ...] | None = None
    for source_row, row in enumerate(rows, start=first_row):
        if source_row < header_row:
            continue
        if source_row == header_row:
            keys = header_keys(row, first_column)
            continue
        if keys is None:
            # No header row, or the header row lies above the used range.
            keys = column_keys(len(row), first_column)
        payload = row_payload(keys, row, first_column)
        if not payload:
            continue
        yield SheetRow(source_row, keys, row, payload)


def landing_rows(
    rows: Iterable[Sequence[CellValue]],
    first_row: int = 1,
    first_column: int = 0,
    header_row: int = 1,
    span: DateSpan | None = None,
) -> Iterator[LandingRow]:
    """Turn the rows of a sheet into landing rows.

    The rows kept are those of :func:`sheet_payloads`; each is fingerprinted
    by the SHA-256 of its canonical JSON.

    Args:
        rows: the rows of the sheet's used range, top to bottom.
        first_row: Excel row number of the first row in ``rows``.
        first_column: 0-based index of the first column of every row.
        header_row: 1-based header row; ``0`` when the sheet has none.
        span: when given, it is shown the cell of its column in every kept row.
            It never changes what is yielded.

    Yields:
        One :class:`LandingRow` per kept row, carrying its real Excel row number.
    """
    for kept in spanned(sheet_payloads(rows, first_row, first_column, header_row), span):
        payload_json = canonical_json(kept.payload)
        yield LandingRow(kept.source_row, sha256_hex(payload_json.encode("utf-8")), payload_json)


def spanned(rows: Iterable[SheetRow], span: DateSpan | None) -> Iterator[SheetRow]:
    """Pass kept rows on, showing a span the cell of its column in each.

    The command-line loader and the web app's validation both read the period
    of a load this way, so a file gives the same period whichever path it
    takes.

    Args:
        rows: kept rows, as :func:`sheet_payloads` yields them.
        span: the span to show the dated column to; ``None`` passes the rows on.

    Yields:
        The same rows, unchanged.
    """
    keys: tuple[str, ...] | None = None
    watched = -1
    for kept in rows:
        if span is not None:
            if kept.keys is not keys:
                keys = kept.keys
                watched = _watched_column(keys, span)
            if 0 <= watched < len(kept.cells):
                span.observe(kept.cells[watched])
        yield kept


# -----------------------------------------------------------------------------
# Archive
# -----------------------------------------------------------------------------
def archive_object_key(
    plant_code: str,
    source_key: str,
    content_hash: str,
    extension: str,
    stored_on: date,
) -> str:
    """Build the object key an archived workbook is stored under.

    The key carries the provenance and ends in the content hash, so two
    different files can never collide and the same file archived twice on one
    day lands on the same key.

    Args:
        plant_code: the plant the file belongs to.
        source_key: the source it was first loaded for.
        content_hash: SHA-256 hex digest of the file.
        extension: file extension, with or without the leading dot.
        stored_on: the date the file is archived, UTC.

    Returns:
        ``{plant_code}/{source_key}/{YYYY}/{MM}/{DD}/{content_hash}.{extension}``.
    """
    suffix = extension.lstrip(".").lower() or "bin"
    return f"{plant_code}/{source_key}/{stored_on:%Y/%m/%d}/{content_hash}.{suffix}"


__all__ = [
    "DEFAULT_PLANT_CODE",
    "FORBIDDEN_SHEETS",
    "LANDING_SOURCES",
    "PERIOD_KEY",
    "SOURCE_SYSTEM_EXCEL",
    "SOURCE_SYSTEM_QAD",
    "CellValue",
    "DateSpan",
    "LandingRow",
    "LandingSource",
    "SheetRow",
    "SheetSpec",
    "archive_object_key",
    "canonical_json",
    "cell_text",
    "column_keys",
    "column_letter",
    "header_keys",
    "is_valid_dataset_key",
    "landing_rows",
    "landing_source",
    "parse_sheet_spec",
    "row_payload",
    "sha256_hex",
    "sheet_payloads",
    "spanned",
]
