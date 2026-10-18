"""Tests for the monthly energy workbook: its rules and its adapter.

Layer: tests (unit)

The workbook is built with openpyxl, laid out as ``Base_de_datos.xlsx`` is: a
row of notes, the headers on row 2, and per month one row per process with the
area's total row among them. Only the total row's ``KWH`` may be read; the
process rows carry an allocation of it in ``KWH por proceso``.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from openpyxl import Workbook

from energy.adapters.energy_book import BookSheet, EnergyBookSource
from energy.domain.energy_book import BookRow, book_months, month_number
from energy.domain.equs_gold import DateRange
from energy.domain.exceptions import (
    DomainRuleError,
    InvalidPeriodError,
    SourceUnavailableError,
    UnsupportedFileFormatError,
)
from energy.ports.energy_cross import AreaEnergySource

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

pytestmark = pytest.mark.unit


# =============================================================================
# Rules
# =============================================================================
@pytest.mark.parametrize(
    ("label", "number"),
    [("Ene", 1), ("Jan", 1), ("Abr", 4), ("Apr", 4), ("Ago", 8), ("Aug", 8), ("Dic", 12),
     ("Dec", 12), (" sep ", 9)],
)  # fmt: skip
def test_month_labels_are_read_in_spanish_and_english(label: str, number: int) -> None:
    assert month_number(label) == number


def test_a_label_that_is_not_a_month_is_refused() -> None:
    with pytest.raises(DomainRuleError):
        month_number("Total")


def _rows(*labels: tuple[int, str]) -> list[BookRow]:
    return [BookRow(row, year, month, Decimal(row)) for row, (year, month) in enumerate(labels, 3)]


def test_a_december_takes_the_year_of_the_november_before_it() -> None:
    """OREC writes 2024 Dic between 2023 Nov and 2024 Ene: it is December 2023."""
    months = book_months(_rows((2023, "Nov"), (2024, "Dic"), (2024, "Ene")))

    assert list(months) == [date(2023, 11, 1), date(2023, 12, 1), date(2024, 1, 1)]
    assert months[date(2023, 12, 1)] == Decimal(4)


def test_a_december_already_labelled_right_stays_right() -> None:
    months = book_months(_rows((2023, "Nov"), (2023, "Dic"), (2024, "Ene")))

    assert list(months) == [date(2023, 11, 1), date(2023, 12, 1), date(2024, 1, 1)]


@pytest.mark.parametrize(
    "labels",
    [
        ((2024, "Ene"), (2024, "Mar")),
        ((2024, "Ene"), (2024, "Ene")),
        ((2023, "Nov"), (2023, "Dic"), (2023, "Ene")),
    ],
    ids=["a gap", "a repeat", "a year that goes back"],
)
def test_months_that_do_not_follow_one_another_are_refused(
    labels: tuple[tuple[int, str], ...],
) -> None:
    with pytest.raises(DomainRuleError, match="do not follow"):
        book_months(_rows(*labels))


# =============================================================================
# Adapter
# =============================================================================
HEADER = ("Año", "Mes", "Dia", "Turno", "Proceso ", "Indice", "KWH", "KWH por proceso",
          "Produccion (Equivalente)")  # fmt: skip


def _workbook(path: Path, *, header: Sequence[str] = HEADER) -> Path:
    """OREC with two processes and its total, Oct 2025 to Jan 2026, December labelled 2026."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "OREC"
    sheet.append(["", "", "", "", "", "", "", "", "", "Va cambiando"])
    sheet.append(list(header))
    for year, month, kwh in ((2025, "Oct", 500.0), (2025, "Nov", 600.0), (2026, "Dic", 700.0),
                             (2026, "Ene", 800.0)):  # fmt: skip
        sheet.append([year, month, "N/A", "N/A", "Rejillas", 0.25, kwh, kwh * 0.25, ""])
        sheet.append([year, month, "N/A", "N/A", "Cinta", 0.75, kwh, kwh * 0.75, ""])
        sheet.append([year, month, "N/A", "N/A", "OREC", 1.0, kwh, kwh, 1000])
    sheet.append([None] * 9)  # trailing blank rows, as OREC has
    english = workbook.create_sheet("RC Energia")
    english.append(["notes"])
    english.append(list(header))
    english.append([2025, "Dec", "", "", "RC", 1.0, 321.5, 321.5, 10])
    english.append([2026, "Jan", "", "", "RC", 1.0, "", "", 10])
    buffer = io.BytesIO()
    workbook.save(buffer)
    path.write_bytes(buffer.getvalue())
    return path


def _source(path: Path) -> EnergyBookSource:
    return EnergyBookSource(
        path, [BookSheet("OREC", "OREC", "OREC"), BookSheet("RC", "RC Energia", "RC")]
    )


def test_only_the_total_row_and_its_kwh_are_read(tmp_path: Path) -> None:
    source = _source(_workbook(tmp_path / "book.xlsx"))

    months = source.months("OREC")

    assert isinstance(source, AreaEnergySource)
    assert source.areas == ("OREC", "RC")
    assert months == {
        date(2025, 10, 1): Decimal(500),
        date(2025, 11, 1): Decimal(600),
        date(2025, 12, 1): Decimal(700),
        date(2026, 1, 1): Decimal(800),
    }


def test_english_labels_and_a_blank_kwh_are_read(tmp_path: Path) -> None:
    """A blank KWH is a month without a figure: missing, never 0."""
    months = _source(_workbook(tmp_path / "book.xlsx")).months("RC")

    assert months == {date(2025, 12, 1): Decimal("321.5"), date(2026, 1, 1): None}


def test_the_energy_of_an_area_is_by_whole_month(tmp_path: Path) -> None:
    source = _source(_workbook(tmp_path / "book.xlsx"))

    energy = source.energy("OREC", DateRange(date(2025, 11, 10), date(2026, 1, 31)))

    assert energy.resolution == "month"
    assert energy.source == "book.xlsx"
    assert [(span.start, span.kwh) for span in energy.months] == [
        (date(2025, 11, 10), None),
        (date(2025, 12, 1), Decimal(700)),
        (date(2026, 1, 1), Decimal(800)),
    ]


def test_an_area_without_a_sheet_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InvalidPeriodError):
        _source(_workbook(tmp_path / "book.xlsx")).energy(
            "F&A", DateRange(date(2026, 1, 1), date(2026, 1, 31))
        )


def test_a_missing_workbook_is_reported(tmp_path: Path) -> None:
    with pytest.raises(SourceUnavailableError):
        _source(tmp_path / "absent.xlsx").months("OREC")


def test_a_sheet_without_the_kwh_header_is_refused(tmp_path: Path) -> None:
    path = _workbook(tmp_path / "book.xlsx", header=[name.replace("KWH", "kW") for name in HEADER])

    with pytest.raises(UnsupportedFileFormatError, match="lacks a header"):
        _source(path).months("OREC")
