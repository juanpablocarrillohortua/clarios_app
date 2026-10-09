"""Tests for the rules that turn a spreadsheet row into a Bronze landing row.

Layer: tests (unit)

Silver reads Bronze payloads by key and compares their hashes, so these rules
are a contract: a change in how a number is written or a repeated header is
named silently changes every hash and breaks every lookup downstream.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta

import pytest

from energy.domain.exceptions import ConfigurationError, UnknownSourceError
from energy.domain.landing import (
    LANDING_SOURCES,
    DateSpan,
    SheetSpec,
    archive_object_key,
    canonical_json,
    cell_text,
    column_keys,
    column_letter,
    header_keys,
    landing_rows,
    landing_source,
    parse_sheet_spec,
    row_payload,
    sha256_hex,
)

pytestmark = pytest.mark.unit


# -----------------------------------------------------------------------------
# Cell text
# -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Strings are kept exactly: not trimmed, and "N/A" stays "N/A".
        ("N/A", "N/A"),
        ("  padded  ", "  padded  "),
        ("21500000A", "21500000A"),
        # Whole numbers lose the ".0" Excel never showed.
        (900.0, "900"),
        (17240425.0, "17240425"),
        (0.0, "0"),
        (-0.0, "0"),
        (7, "7"),
        (1e22, "10000000000000000000000"),
        # Other numbers: shortest exact decimal, never scientific notation.
        (0.0636, "0.0636"),
        (1e-05, "0.00001"),
        (-0.000992, "-0.000992"),
        (182346891.593783, "182346891.593783"),
        (2604.9555941969, "2604.9555941969"),
        (1.5e-07, "0.00000015"),
        # Booleans.
        (True, "true"),
        (False, "false"),
        # Dates and times are ISO-8601.
        (date(2025, 12, 10), "2025-12-10"),
        (datetime(2025, 12, 10, 11, 34, 5), "2025-12-10T11:34:05"),  # noqa: DTZ001 - as a reader returns it
        (time(11, 34), "11:34:00"),
        (timedelta(hours=1, minutes=30), "PT1H30M0S"),
    ],
)
def test_cell_text(value: object, expected: str) -> None:
    """Each kind of cell is written the one way the contract allows."""
    assert cell_text(value) == expected


@pytest.mark.parametrize("empty", [None, ""])
def test_empty_cell_has_no_text(empty: object) -> None:
    """An empty cell yields nothing, so the payload omits its key."""
    assert cell_text(empty) is None


# -----------------------------------------------------------------------------
# Keys
# -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("index", "letter"),
    [(0, "A"), (25, "Z"), (26, "AA"), (51, "AZ"), (52, "BA"), (701, "ZZ"), (702, "AAA")],
)
def test_column_letter(index: int, letter: str) -> None:
    """Column letters follow Excel: A to Z, then AA, AB and so on."""
    assert column_letter(index) == letter


def test_column_letter_rejects_a_negative_index() -> None:
    """There is no column before A."""
    with pytest.raises(ValueError, match="negative"):
        column_letter(-1)


def test_header_keys_name_blank_and_repeated_headers() -> None:
    """A blank header takes its column letter and a repeated one a suffix.

    The QAD product-structure export has two columns headed ``Descripción``; a
    JSON object cannot hold both under one key, and dropping either loses data.
    """
    header = ["Artículo Matriz", "Descripción", "Descripción", "", None, " Estado ", "Descripción"]

    assert header_keys(header) == (
        "Artículo Matriz",
        "Descripción",
        "Descripción #2",
        "D",
        "E",
        "Estado",
        "Descripción #3",
    )


def test_header_keys_use_the_real_column_of_a_sheet_that_starts_late() -> None:
    """A blank header in a sheet that starts in column B is named by its true letter."""
    assert header_keys(["Planta ", "", "Línea"], first_column=1) == ("Planta", "C", "Línea")


def test_header_keys_never_repeat_even_when_a_suffix_is_taken() -> None:
    """A header that already looks like a generated key does not cause a clash."""
    assert header_keys(["X", "X #2", "X"]) == ("X", "X #2", "X #3")


def test_header_keys_render_a_non_text_header() -> None:
    """A date used as a header becomes its ISO text."""
    assert header_keys([date(2021, 10, 1), 2021.0]) == ("2021-10-01", "2021")


def test_column_keys_for_a_sheet_without_headers() -> None:
    """With no header row every key is a column letter."""
    assert column_keys(3) == ("A", "B", "C")
    assert column_keys(2, first_column=26) == ("AA", "AB")


def test_row_payload_omits_empty_cells_and_keys_the_overflow_by_letter() -> None:
    """Empty cells leave no key; cells beyond the header are still kept."""
    payload = row_payload(("Código", "Cantidad"), ["21500000A", 60.0, "", None, "stray"])

    assert payload == {"Código": "21500000A", "Cantidad": "60", "E": "stray"}


# -----------------------------------------------------------------------------
# Fingerprints
# -----------------------------------------------------------------------------
def test_canonical_json_is_sorted_compact_and_unescaped() -> None:
    """Equal payloads give equal text, whatever order the columns were read in."""
    first = canonical_json({"b": "1", "Número": "ñ"})
    second = canonical_json({"Número": "ñ", "b": "1"})

    assert first == second == '{"Número":"ñ","b":"1"}'


def test_landing_rows_carry_row_numbers_hashes_and_keep_duplicates() -> None:
    """Empty rows go; everything else stays, with its real Excel row number.

    The repeated row and the header pasted again further down are real defects
    of the exports. Bronze keeps both: removing them is a Silver decision.
    """
    rows = [
        ["Artículo Matriz", "Cantidad Por"],
        ["215000015", 0.07171],
        ["", ""],
        ["215000015", 0.07171],
        ["Artículo Matriz", "Cantidad Por"],
    ]

    landed = list(landing_rows(rows))

    assert [row.source_row for row in landed] == [2, 4, 5]
    assert json.loads(landed[0].payload_json) == {
        "Artículo Matriz": "215000015",
        "Cantidad Por": "0.07171",
    }
    assert landed[0].content_hash == landed[1].content_hash
    assert json.loads(landed[2].payload_json) == {
        "Artículo Matriz": "Artículo Matriz",
        "Cantidad Por": "Cantidad Por",
    }
    expected = hashlib.sha256(landed[0].payload_json.encode("utf-8")).hexdigest()
    assert landed[0].content_hash == expected == sha256_hex(landed[0].payload_json.encode())


def test_landing_rows_without_a_header_row() -> None:
    """Header row 0 means every row is data and every key a column letter."""
    landed = list(landing_rows([["OXPROD_KG", "", "OXIDO"]], header_row=0))

    assert [row.source_row for row in landed] == [1]
    assert json.loads(landed[0].payload_json) == {"A": "OXPROD_KG", "C": "OXIDO"}


def test_landing_rows_skip_what_lies_above_the_header_row() -> None:
    """Rows above the header are titles, not data."""
    rows = [["CONSOLIDADO", ""], ["", ""], ["GRUPO", "CANT"], ["LINEA 1", 98978.0]]

    landed = list(landing_rows(rows, header_row=3))

    assert [row.source_row for row in landed] == [4]
    assert json.loads(landed[0].payload_json) == {"GRUPO": "LINEA 1", "CANT": "98978"}


def test_landing_rows_honour_where_the_used_range_starts() -> None:
    """A sheet that starts in column B, row 1 keeps Excel's own coordinates."""
    rows = [["Planta ", "Línea"], ["RC", "RCREFI02"]]

    landed = list(landing_rows(rows, first_row=1, first_column=1))

    assert [row.source_row for row in landed] == [2]
    assert json.loads(landed[0].payload_json) == {"Planta": "RC", "Línea": "RCREFI02"}


def test_landing_rows_fall_back_to_letters_when_the_header_row_is_empty() -> None:
    """If the used range starts below the header row, keys are column letters."""
    landed = list(landing_rows([["a", "b"]], first_row=5, first_column=2, header_row=1))

    assert [row.source_row for row in landed] == [5]
    assert json.loads(landed[0].payload_json) == {"C": "a", "D": "b"}


# -----------------------------------------------------------------------------
# The period a load covers
# -----------------------------------------------------------------------------
MOVEMENTS = [
    ["Número de transacción", "Fecha Efectiva", "Cantidad"],
    [17240425.0, datetime(2024, 3, 15, 0, 0), 70000.0],  # noqa: DTZ001 - Excel has no time zones
    [17240418.0, date(2023, 10, 2), 35000.0],
    ["", "", ""],
    [17240501.0, date(2024, 9, 30), 1.0],
]


def test_date_span_finds_the_first_and_last_date_of_its_column() -> None:
    """Whatever years the rows hold, the span reports them; nobody states a year."""
    span = DateSpan("Fecha Efectiva")

    landed = list(landing_rows(MOVEMENTS, span=span))

    assert len(landed) == 3
    assert span.bounds() == (date(2023, 10, 2), date(2024, 9, 30))
    assert (span.first, span.last) == span.bounds()


def test_date_span_counts_only_cells_the_workbook_holds_as_dates() -> None:
    """Text is never parsed, so a typed date, a number or a pasted header is ignored."""
    rows = [
        ["Fecha Efectiva", "Cantidad"],
        ["2031-01-01", 1.0],
        ["15/03/2031", 1.0],
        [45000.0, 1.0],
        ["Fecha Efectiva", "Cantidad"],
        [date(2025, 12, 10), 1.0],
        [True, 1.0],
        ["", 1.0],
    ]
    span = DateSpan("Fecha Efectiva")

    landed = list(landing_rows(rows, span=span))

    assert len(landed) == 7
    assert span.bounds() == (date(2025, 12, 10), date(2025, 12, 10))


def test_date_span_stays_empty_without_its_column_or_without_dates() -> None:
    """No such header, no header row at all, or no date cell: the period is unknown."""
    absent = DateSpan("Fecha Efectiva")
    list(landing_rows([["Artículo Matriz"], [date(2025, 1, 1)]], span=absent))
    headerless = DateSpan("Fecha Efectiva")
    list(landing_rows([[date(2025, 1, 1)]], header_row=0, span=headerless))
    textual = DateSpan("Fecha Efectiva")
    list(landing_rows([["Fecha Efectiva"], ["2025-01-01"]], span=textual))

    assert absent.bounds() == headerless.bounds() == textual.bounds() == (None, None)


def test_date_span_follows_a_sheet_that_starts_in_a_later_column() -> None:
    """The column is found by its header, wherever the used range begins."""
    rows = [["Planta", "Fecha Efectiva"], ["RC", date(2026, 2, 1)], ["RC"]]
    span = DateSpan("Fecha Efectiva")

    list(landing_rows(rows, first_column=3, span=span))

    assert span.bounds() == (date(2026, 2, 1), date(2026, 2, 1))


def test_watching_the_dates_changes_no_landing_row() -> None:
    """The payload rules are a contract: a span must not alter a hash or a payload."""
    assert list(landing_rows(MOVEMENTS, span=DateSpan("Fecha Efectiva"))) == list(
        landing_rows(MOVEMENTS)
    )


def test_one_span_covers_several_sheets() -> None:
    """A load with more than one dated sheet reports a single period."""
    span = DateSpan("Fecha Efectiva")

    list(landing_rows([["Fecha Efectiva"], [date(2025, 5, 1)]], span=span))
    list(landing_rows([["x", "Fecha Efectiva"], ["a", date(2022, 1, 9)]], span=span))

    assert span.bounds() == (date(2022, 1, 9), date(2025, 5, 1))


# -----------------------------------------------------------------------------
# Sources and sheet specifications
# -----------------------------------------------------------------------------
def test_landing_sources_cover_the_keys_the_ledger_accepts() -> None:
    """The loader knows exactly the source keys the Bronze check constraint allows."""
    assert set(LANDING_SOURCES) == {
        "qad_rct_wo",
        "qad_facturas",
        "qad_product_struc",
        "item_master",
        "manual_sheets",
    }
    assert landing_source("qad_rct_wo").source_system == "qad"
    assert landing_source("manual_sheets").source_system == "excel"
    assert landing_source("manual_sheets").needs_dataset_key
    assert landing_source("qad_product_struc").default_sheets == ()
    assert [sheet.name for sheet in landing_source("item_master").default_sheets] == [
        "Datos",
        "Grupo-Fam",
    ]


def test_only_the_movement_sources_are_dated() -> None:
    """Receipts and invoices happen on a date; structures and catalogues are snapshots."""
    dated = {key: source.period_key for key, source in LANDING_SOURCES.items()}

    assert dated == {
        "qad_rct_wo": "Fecha Efectiva",
        "qad_facturas": "Fecha Efectiva",
        "qad_product_struc": None,
        "item_master": None,
        "manual_sheets": None,
    }


def test_unknown_source_key_is_refused() -> None:
    """A source key outside the list is an error, not a new table."""
    with pytest.raises(UnknownSourceError):
        landing_source("sap_export")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Datos", SheetSpec("Datos")),
        ("BAT ENS", SheetSpec("BAT ENS")),
        ("Clasif_Poly=clasif_poly", SheetSpec("Clasif_Poly", "clasif_poly")),
        ("INSTRUCC=instrucc:0", SheetSpec("INSTRUCC", "instrucc", 0)),
        ("L.PRODUCCION=l_produccion:3", SheetSpec("L.PRODUCCION", "l_produccion", 3)),
        ("Datos:2", SheetSpec("Datos", None, 2)),
        # An equals sign that does not introduce a dataset key is part of the name.
        ("A=B", SheetSpec("A=B")),
        ("  ELEMENT DECO = element_deco ", SheetSpec("ELEMENT DECO", "element_deco")),
    ],
)
def test_parse_sheet_spec(text: str, expected: SheetSpec) -> None:
    """A sheet is given as NAME[=dataset_key][:header_row]."""
    assert parse_sheet_spec(text) == expected


def test_parse_sheet_spec_uses_the_default_header_row() -> None:
    """A sheet that names no header row takes the invocation's."""
    assert parse_sheet_spec("INSTRUCC=instrucc", default_header_row=0).header_row == 0


@pytest.mark.parametrize("text", ["", "  ", "Datos:x", "=clasif_poly", ":3"])
def test_parse_sheet_spec_rejects_what_it_cannot_read(text: str) -> None:
    """An empty name or a non-numeric header row is refused."""
    with pytest.raises(ConfigurationError):
        parse_sheet_spec(text)


def test_archive_object_key_carries_provenance_and_ends_in_the_hash() -> None:
    """The key says whose file it is and cannot collide with another file's."""
    key = archive_object_key("1515", "qad_rct_wo", "ab" * 32, ".XLSX", date(2026, 10, 7))

    assert key == f"1515/qad_rct_wo/2026/10/07/{'ab' * 32}.xlsx"
