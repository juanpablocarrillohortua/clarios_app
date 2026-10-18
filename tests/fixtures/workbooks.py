"""Synthetic workbooks for the upload and validation tests. No real data.

Each builder returns the bytes of an ``.xlsx`` made up for a test, and the
broken ones also return the issues they were built to show: their manifest,
as ``(row, column letter, rule)``. The tests compare what the validation finds
with the manifest, and ``scripts/make_test_workbooks.py`` writes the same
files to a folder, with the manifest beside them, for checks by hand.

:func:`write_padded_workbook` writes a valid ``.xlsx`` package of an exact
size, padded with random bytes in an entry nothing reads: the files of 300 MiB
used to measure an upload. Random bytes do not compress, so the size on disk is
the size sent.
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from openpyxl import Workbook  # type: ignore[import-untyped]

from energy.adapters.xlsx_dataset_template import XlsxDatasetTemplateWriter

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from energy.domain.dataset_spec import DatasetSpec

type Planted = tuple[int | None, str | None, str]

LINES_SHEET: Final = "Lineas Produccion"
INVOICES_SHEET: Final = "Datos"
FILLER_ENTRY: Final = "xl/media/filler.bin"
#: The time written into every package, so equal content gives equal bytes.
_FIXED_TIME: Final = (2026, 1, 1, 0, 0, 0)
_CORE_PROPERTIES: Final = "docProps/core.xml"
_STAMP: Final = re.compile(rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_CHUNK: Final = 1024 * 1024

LINES_HEADERS: Final = (
    "Línea",
    "Almac",
    "Descripción",
    "Planificación de Unidades por Hora",
    "Ubicación de Posdeducción",
    "Método de cantidad de transacción",
    "Programador",
    "Tiempo de configuración  (D H:M:S)",
    "Firma Automática",
    "Última Firma Automática",
)


def build_workbook(sheets: dict[str, Sequence[Sequence[Any]]]) -> bytes:
    """Return an ``.xlsx`` with the given sheets and rows, in order."""
    workbook = Workbook(write_only=True)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return _without_timestamps(buffer.getvalue())


def _without_timestamps(package: bytes) -> bytes:
    """Return ``package`` with its timestamps fixed.

    openpyxl writes the moment of saving into ``docProps/core.xml``, and
    ``zipfile`` the time of each entry, both to the second. Two builds of the
    same rows a second apart would otherwise differ, and so would their hashes,
    which the tests of duplicates and of the purge compare.
    """
    target = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(package)) as source,
        zipfile.ZipFile(target, "w") as rebuilt,
    ):
        for info in source.infolist():
            data = source.read(info)
            if info.filename == _CORE_PROPERTIES:
                data = _STAMP.sub(b"2026-01-01T00:00:00Z", data)
            entry = zipfile.ZipInfo(info.filename, date_time=_FIXED_TIME)
            entry.compress_type = info.compress_type
            rebuilt.writestr(entry, data)
    return target.getvalue()


def lines_good() -> bytes:
    """Return a production-lines catalogue that breaks no rule."""
    return build_workbook(
        {
            LINES_SHEET: [
                LINES_HEADERS,
                ["FAFORM01", "1010", "Formación 1", 120.5, "", "", "", "1 02:30:00", "Sí", ""],
                ["ENSAM02", "1010", "Ensamble 2", 80, "", "", "", "0 01:00:00", "No", ""],
            ]
        }
    )


def lines_broken() -> tuple[bytes, list[Planted]]:
    """Return a production-lines catalogue broken on purpose, and its manifest.

    The header writes ``Descripcion`` without its accent, leaves out
    ``Última Firma Automática`` and adds ``Comentarios``; then one row breaks
    each rule a cell can break.
    """
    header = [
        "Línea",
        "Almac",
        "Descripcion",
        "Planificación de Unidades por Hora",
        "Ubicación de Posdeducción",
        "Método de cantidad de transacción",
        "Programador",
        "Tiempo de configuración  (D H:M:S)",
        "Firma Automática",
        "Comentarios",
    ]

    def row(**changes: Any) -> list[Any]:
        values: dict[str, Any] = {
            "A": "FAFORM01",
            "B": "1010",
            "C": "Formación",
            "D": 100,
            "H": "1 02:30:00",
            "I": "Sí",
        }
        values.update(changes)
        return [values.get(letter, "") for letter in "ABCDEFGHIJ"]

    rows = [
        header,
        row(),  # 2: sound
        row(A=""),  # 3
        row(A="faform02"),  # 4
        row(B="101"),  # 5
        row(D="doce"),  # 6
        row(D=-5),  # 7
        row(H="2:30"),  # 8
        row(I="Tal vez"),  # 9
        row(J="ok"),  # 10: sound, with a value in the unknown column
    ]
    manifest: list[Planted] = [
        (1, "C", "schema.header_mismatch"),
        (1, None, "schema.column_missing"),
        (1, "J", "schema.column_unknown"),
        (3, "A", "cell.required"),
        (4, "A", "cell.pattern"),
        (5, "B", "cell.pattern"),
        (6, "D", "cell.type"),
        (7, "D", "cell.range"),
        (8, "H", "cell.pattern"),
        (9, "I", "cell.allowed"),
    ]
    return build_workbook({LINES_SHEET: rows}), manifest


def invoices_broken(spec: DatasetSpec) -> tuple[bytes, list[Planted]]:
    """Return an invoices export with every header, broken on purpose, and its manifest.

    Args:
        spec: the ``qad_facturas`` spec, whose headers the file carries.
    """
    sound: dict[str, Any] = {
        "Almac": "1010",
        "Núm Fact": "F-0001",
        "Orden": "OV-1",
        "Línea": 1,
        "Línea de productos": "1200",
        "Número Artículo": "TEST-ITEM",
        "Cantidad fac": 10,
        "Porcentaje Descuento": 5,
        "Moneda": "COP",
        "Fecha Efectiva": datetime(2025, 10, 1),
        "HORA_LIBERACION": "08:00:00",
    }
    position = {column.key: index for index, column in enumerate(spec.columns)}

    def row(**changes: Any) -> list[Any]:
        values = {**sound, **changes}
        cells: list[Any] = [""] * len(spec.columns)
        for key, value in values.items():
            cells[position[key]] = value
        return cells

    def letter(key: str) -> str:
        from energy.domain.landing import column_letter  # noqa: PLC0415

        return column_letter(position[key])

    rows = [
        list(spec.headers),
        row(),  # 2: sound
        row(**{"Fecha Efectiva": "01/10/2025"}),  # 3: a date typed as text
        row(**{"Línea": 1.5}),  # 4: not a whole number
        row(**{"Línea": 0}),  # 5: below 1
        row(**{"Porcentaje Descuento": 150}),  # 6: above 100
        row(Moneda="cop"),  # 7
        row(**{"Núm Fact": ""}),  # 8
        row(**{"Fecha Efectiva": 45992}),  # 9: a date that lost its format
    ]
    manifest: list[Planted] = [
        (3, letter("Fecha Efectiva"), "cell.type"),
        (4, letter("Línea"), "cell.type"),
        (5, letter("Línea"), "cell.range"),
        (6, letter("Porcentaje Descuento"), "cell.range"),
        (7, letter("Moneda"), "cell.pattern"),
        (8, letter("Núm Fact"), "cell.required"),
        (9, letter("Fecha Efectiva"), "cell.type"),
    ]
    return build_workbook({INVOICES_SHEET: rows}), manifest


def write_padded_workbook(path: Path, size: int, spec: DatasetSpec) -> None:
    """Write a valid ``.xlsx`` of exactly ``size`` bytes, padded with random bytes.

    The package is the spec's template, plus one stored entry of random bytes
    that no part refers to. It is written a megabyte at a time.

    Args:
        path: the file to write.
        size: its size in bytes.
        spec: the dataset whose template the package holds.

    Raises:
        ValueError: if ``size`` is smaller than the template itself.
    """
    template = XlsxDatasetTemplateWriter().write(spec)
    with zipfile.ZipFile(io.BytesIO(template)) as source:
        parts = [(info, source.read(info)) for info in source.infolist()]

    def write(target: Any, filler: int) -> None:
        with zipfile.ZipFile(target, "w") as package:
            for info, data in parts:
                package.writestr(info, data)
            entry = zipfile.ZipInfo(FILLER_ENTRY, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_STORED
            entry.file_size = filler
            with package.open(entry, "w") as padding:
                left = filler
                while left:
                    chunk = os.urandom(min(_CHUNK, left))
                    padding.write(chunk)
                    left -= len(chunk)

    empty = io.BytesIO()
    write(empty, 0)
    filler = size - len(empty.getvalue())
    if filler < 0:
        msg = f"a package needs at least {len(empty.getvalue())} bytes"
        raise ValueError(msg)
    with path.open("wb") as target:
        write(target, filler)


__all__ = [
    "FILLER_ENTRY",
    "INVOICES_SHEET",
    "LINES_HEADERS",
    "LINES_SHEET",
    "Planted",
    "build_workbook",
    "invoices_broken",
    "lines_broken",
    "lines_good",
    "write_padded_workbook",
]
