"""Tests for reading what a QAD export says about itself.

Layer: tests (unit)

The timestamp cases are the three spellings found in the real workbooks: QAD
writes the same field with and without a 12-hour suffix depending on the
machine the export was saved on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from energy.domain.qad_export import (
    export_info,
    info_dictionary,
    parse_qad_timestamp,
    search_conditions,
)

pytestmark = pytest.mark.unit

BOGOTA = ZoneInfo("America/Bogota")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1/10/2026 8:03:42 a. m.", datetime(2026, 10, 1, 8, 3, 42, tzinfo=BOGOTA)),
        ("01/10/2026 8:13:24", datetime(2026, 10, 1, 8, 13, 24, tzinfo=BOGOTA)),
        ("15/02/2021 11:20:34 p. m.", datetime(2021, 2, 15, 23, 20, 34, tzinfo=BOGOTA)),
        # Midnight and noon on the 12-hour clock.
        ("15/02/2021 12:00:00 a. m.", datetime(2021, 2, 15, 0, 0, 0, tzinfo=BOGOTA)),
        ("15/02/2021 12:00:00 p. m.", datetime(2021, 2, 15, 12, 0, 0, tzinfo=BOGOTA)),
        # A 24-hour afternoon, non-breaking spaces, and a suffix without dots.
        ("15/04/2021 23:34:36", datetime(2021, 4, 15, 23, 34, 36, tzinfo=BOGOTA)),
        ("15/04/2021 11:34:36\u00a0a.\u00a0m.", datetime(2021, 4, 15, 11, 34, 36, tzinfo=BOGOTA)),
        ("15/04/2021 11:34:36 PM", datetime(2021, 4, 15, 23, 34, 36, tzinfo=BOGOTA)),
    ],
)
def test_parse_qad_timestamp(text: str, expected: datetime) -> None:
    """A QAD timestamp is read as day/month/year in Bogota time."""
    parsed = parse_qad_timestamp(text)

    assert parsed == expected
    assert parsed is not None
    assert parsed.utcoffset() == expected.utcoffset()


def test_parse_qad_timestamp_is_bogota_time() -> None:
    """Eight in the morning in Bogota is one in the afternoon UTC."""
    parsed = parse_qad_timestamp("1/10/2026 8:03:42 a. m.")

    assert parsed is not None
    assert parsed.astimezone(UTC) == datetime(2026, 10, 1, 13, 3, 42, tzinfo=UTC)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "not a date",
        "2026-10-01 08:03:42",
        "32/10/2026 8:03:42",
        "1/13/2026 8:03:42",
        "1/10/2026 13:03:42 p. m.",
        "1/10/2026 0:03:42 a. m.",
        "1/10/2026 25:03:42",
    ],
)
def test_unreadable_timestamp_is_none(text: str | None) -> None:
    """A timestamp that cannot be read is stored as null, never guessed."""
    assert parse_qad_timestamp(text) is None


def test_info_dictionary_and_export_info() -> None:
    """The Info sheet gives the program, the exploration, the user and the moment."""
    rows = [
        ["Nombre", "Valor"],
        ["ID del Usuario", "ptorrm1"],
        ["Fecha de consulta", "01/10/2026 8:13:24"],
        ["Nombre Exploración", "Explorar Transacciones por Art"],
        ["Programa Exploración", "icbr011.p"],
        ["", ""],
        ["Sin valor", ""],
    ]

    info = info_dictionary(rows)
    export = export_info(info)

    assert info == {
        "ID del Usuario": "ptorrm1",
        "Fecha de consulta": "01/10/2026 8:13:24",
        "Nombre Exploración": "Explorar Transacciones por Art",
        "Programa Exploración": "icbr011.p",
        "Sin valor": "",
    }
    assert export.program == "icbr011.p"
    assert export.exploration == "Explorar Transacciones por Art"
    assert export.user == "ptorrm1"
    assert export.queried_at == datetime(2026, 10, 1, 8, 13, 24, tzinfo=BOGOTA)


def test_export_info_of_a_workbook_without_an_info_sheet() -> None:
    """A hand-made workbook says nothing about a query."""
    export = export_info({})

    assert (export.program, export.exploration, export.user, export.queried_at) == (
        None,
        None,
        None,
        None,
    )


def test_search_conditions_use_lowercase_keys_and_omit_empty_cells() -> None:
    """The conditions are kept as delivered, one object per row."""
    rows = [
        ["Campo", "Operador", "Valor", "Monto Hasta"],
        ["Fecha Efectiva", "rango", "2025-10-01", "2025-12-31"],
        ["Tipo Transacción", "es igual a", "RCT-WO", ""],
        ["", "", "", ""],
    ]

    assert search_conditions(rows) == [
        {
            "campo": "Fecha Efectiva",
            "operador": "rango",
            "valor": "2025-10-01",
            "monto hasta": "2025-12-31",
        },
        {"campo": "Tipo Transacción", "operador": "es igual a", "valor": "RCT-WO"},
    ]
