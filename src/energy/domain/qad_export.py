"""What a QAD export says about itself.

Layer: domain

Responsibility:
    A workbook exported from a QAD exploration carries two sheets beside its
    data: *Info*, which names the exploration, the program, the user and the
    moment the query ran, and *Condiciones de búsqueda*, which lists the filters
    the user applied. This module reads both into plain values. It reports what
    the export claims and nothing more: the conditions are known to be stale in
    at least one file, so they are kept as delivered and nothing is derived from
    them, and a timestamp that cannot be read becomes ``None`` rather than a
    guess.

Depends on:
    ``energy.domain.landing``, standard library only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from energy.domain.landing import cell_text

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from energy.domain.landing import CellValue

INFO_SHEET: Final = "Info"
CONDITIONS_SHEET: Final = "Condiciones de búsqueda"

# Row names of the Info sheet.
INFO_PROGRAM: Final = "Programa Exploración"
INFO_EXPLORATION: Final = "Nombre Exploración"
INFO_USER: Final = "ID del Usuario"
INFO_QUERIED_AT: Final = "Fecha de consulta"

# QAD writes its timestamps in the plant's local time.
QAD_TIMEZONE: Final = "America/Bogota"

_NOON: Final = 12

# dd/mm/yyyy h:mm:ss, optionally followed by "a. m." or "p. m.". The export
# writes the suffix with ordinary or non-breaking spaces, with or without dots.
_QAD_TIMESTAMP: Final = re.compile(
    r"""
    ^\s*
    (?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{4})
    \s+
    (?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2})
    (?:\s*(?P<half>[ap])\.?\s*m\.?)?
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


@lru_cache(maxsize=1)
def _qad_zone() -> ZoneInfo:
    """Return the time zone QAD timestamps are written in."""
    return ZoneInfo(QAD_TIMEZONE)


@dataclass(frozen=True, slots=True)
class QadExportInfo:
    """The identity of the query that produced an export.

    Attributes:
        program: the QAD program, for example ``icbr011.p``.
        exploration: the exploration name.
        user: the QAD user who ran the query.
        queried_at: when the query ran, timezone-aware; ``None`` when the export
            does not say or the text cannot be read.
    """

    program: str | None = None
    exploration: str | None = None
    user: str | None = None
    queried_at: datetime | None = None


def parse_qad_timestamp(text: str | None) -> datetime | None:
    """Read a QAD timestamp.

    Args:
        text: ``dd/mm/yyyy h:mm:ss``, on a 24-hour clock, or on a 12-hour clock
            when followed by ``a. m.`` or ``p. m.``.

    Returns:
        The moment in America/Bogota time, or ``None`` when ``text`` is empty or
        is not a valid timestamp of that form.
    """
    if not text:
        return None
    match = _QAD_TIMESTAMP.match(text)
    if match is None:
        return None

    hour = int(match["hour"])
    half = (match["half"] or "").lower()
    if half:
        if not 1 <= hour <= _NOON:
            return None
        hour %= _NOON
        if half == "p":
            hour += _NOON

    try:
        return datetime(
            int(match["year"]),
            int(match["month"]),
            int(match["day"]),
            hour,
            int(match["minute"]),
            int(match["second"]),
            tzinfo=_qad_zone(),
        )
    except ValueError:
        return None


def info_dictionary(rows: Iterable[Sequence[CellValue]]) -> dict[str, str]:
    """Read the *Info* sheet into a name-to-value mapping.

    Args:
        rows: the rows of the sheet. The first is its header (``Nombre``,
            ``Valor``) and is skipped.

    Returns:
        One entry per named row. A row without a value maps to an empty string.
    """
    info: dict[str, str] = {}
    for index, row in enumerate(rows):
        if index == 0 or not row:
            continue
        name = cell_text(row[0])
        if name is None or not name.strip():
            continue
        value = cell_text(row[1]) if len(row) > 1 else None
        info[name.strip()] = value or ""
    return info


def export_info(info: Mapping[str, str]) -> QadExportInfo:
    """Pick the query identity out of an *Info* mapping.

    Args:
        info: the mapping from :func:`info_dictionary`.

    Returns:
        The :class:`QadExportInfo`; a field the sheet lacks is ``None``.
    """
    return QadExportInfo(
        program=info.get(INFO_PROGRAM) or None,
        exploration=info.get(INFO_EXPLORATION) or None,
        user=info.get(INFO_USER) or None,
        queried_at=parse_qad_timestamp(info.get(INFO_QUERIED_AT)),
    )


def search_conditions(rows: Iterable[Sequence[CellValue]]) -> list[dict[str, str]]:
    """Read the *Condiciones de búsqueda* sheet as delivered.

    Args:
        rows: the rows of the sheet; the first holds the headers.

    Returns:
        One object per condition, keyed by the lowercase header texts
        (``campo``, ``operador``, ``valor``, ``monto hasta``). An empty cell is
        omitted, as it is in a landing payload.
    """
    conditions: list[dict[str, str]] = []
    keys: list[str] = []
    for index, row in enumerate(rows):
        if index == 0:
            keys = [(cell_text(cell) or "").strip().lower() for cell in row]
            continue
        condition = {
            key: text
            for key, cell in zip(keys, row, strict=False)
            if key and (text := cell_text(cell)) is not None
        }
        if condition:
            conditions.append(condition)
    return conditions


__all__ = [
    "CONDITIONS_SHEET",
    "INFO_EXPLORATION",
    "INFO_PROGRAM",
    "INFO_QUERIED_AT",
    "INFO_SHEET",
    "INFO_USER",
    "QAD_TIMEZONE",
    "QadExportInfo",
    "export_info",
    "info_dictionary",
    "parse_qad_timestamp",
    "search_conditions",
]
