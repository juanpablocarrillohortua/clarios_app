"""The energy workbooks, read into training rows: synthetic files with the plant's layout.

The layouts are those of ``data/Base_de_datos.xlsx`` (a title row, then the
headers; one total row per month whose ``Proceso`` is the sheet's name) and
of ``data/SCADA-Base de datos F&A.xlsx`` (hourly counters of three
rectifiers, in Wh). No real data.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from openpyxl import Workbook

from energy.adapters.energy_workbooks import (
    ENERGY_WORKBOOK,
    SCADA_FA_WORKBOOK,
    SCADA_SHEET,
    EnergyWorkbookSource,
    read_monthly_totals,
    read_rectifier_days,
)
from energy.domain.baseline_models import SPECS, ModelKey
from energy.domain.exceptions import SourceUnavailableError

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

HEADERS = [
    None,
    "Año",
    "Mes",
    "Dia",
    "Turno",
    "Proceso ",
    "Indice",
    "KWH",
    "KWH por proceso",
    "Produccion (Equivalente)",
]


def _energy_workbook(path: Path, rows: list[tuple[int, str, str, float, float]]) -> None:
    """Sheet OREC: (year, month label, process, kWh, EQ), with a sub-process row between."""
    book = Workbook()
    sheet = book.active
    sheet.title = "OREC"
    sheet.append(["Energía por proceso"])
    sheet.append(HEADERS)
    for year, month, process, kwh, eq in rows:
        sheet.append([None, year, month, None, None, process, None, kwh, kwh / 2, eq])
        sheet.append([None, year, month, None, None, "Cinta", None, 1.0, 1.0, 1.0])
    book.save(path)


def _scada_workbook(path: Path, start: datetime, hours: int, per_hour: tuple[int, ...]) -> None:
    """Hourly counters starting at ``start``, each rectifier adding its Wh per hour."""
    book = Workbook()
    sheet = book.active
    sheet.title = SCADA_SHEET
    sheet.append(["t_stamp", "RECTIFICADOR 1", "RECTIFICADOR 2", "RECTIFICADOR 3"])
    counters = [10_000_000, 20_000_000, 30_000_000]
    for hour in range(hours):
        sheet.append([start + timedelta(hours=hour), *counters])
        counters = [value + step for value, step in zip(counters, per_hour, strict=True)]
    book.save(path)


def _local(*parts: int) -> datetime:
    """A SCADA stamp: the plant's local time, without a zone, as the workbook holds it."""
    return datetime(*parts)  # noqa: DTZ001


# =============================================================================
# Monthly totals
# =============================================================================
def test_reads_the_total_rows_and_puts_a_mistyped_december_back(tmp_path: Path) -> None:
    """The file's 2026-Dic follows 2025-Nov: it is 2025-12. A zero is missing."""
    path = tmp_path / ENERGY_WORKBOOK
    _energy_workbook(
        path,
        [
            (2025, "Oct", "OREC", 500_000.0, 60_000.0),
            (2025, "Nov", "OREC", 510_000.0, 0.0),
            (2026, "Dic", "OREC", 400_000.0, 40_000.0),
            (2026, "Jan", "OREC", 450_000.0, 50_000.0),
        ],
    )

    assert read_monthly_totals(path, "OREC") == [
        (date(2025, 10, 1), 500_000.0, 60_000.0),
        (date(2025, 11, 1), 510_000.0, None),
        (date(2025, 12, 1), 400_000.0, 40_000.0),
        (date(2026, 1, 1), 450_000.0, 50_000.0),
    ]


def test_monthly_rows_skip_a_missing_month_and_carry_its_days(tmp_path: Path) -> None:
    _energy_workbook(
        tmp_path / ENERGY_WORKBOOK,
        [
            (2026, "Ene", "OREC", 450_000.0, 50_000.0),
            (2026, "Feb", "OREC", 0.0, 52_000.0),
            (2026, "Mar", "OREC", 470_000.0, 55_000.0),
        ],
    )
    source = EnergyWorkbookSource(tmp_path, dict)

    rows = source.rows(SPECS[ModelKey.OREC_MENSUAL])

    assert [(row.period, row.target, dict(row.features)) for row in rows] == [
        (date(2026, 1, 1), 450_000.0, {"eq": 50_000.0, "dias": 31.0}),
        (date(2026, 3, 1), 470_000.0, {"eq": 55_000.0, "dias": 31.0}),
    ]


def test_a_missing_workbook_is_a_source_error(tmp_path: Path) -> None:
    with pytest.raises(SourceUnavailableError):
        read_monthly_totals(tmp_path / ENERGY_WORKBOOK, "OREC")
    with pytest.raises(SourceUnavailableError):
        read_rectifier_days(tmp_path / SCADA_FA_WORKBOOK)


# =============================================================================
# Daily rectifiers
# =============================================================================
def test_a_day_is_the_difference_between_two_midnights(tmp_path: Path) -> None:
    """Stamps off the hour are interpolated at midnight; Wh become kWh."""
    path = tmp_path / SCADA_FA_WORKBOOK
    _scada_workbook(path, _local(2026, 9, 1, 0, 30), 24 * 3, (1_000, 2_000, 3_000))

    days = read_rectifier_days(path)

    assert list(days) == [date(2026, 9, 2)]
    assert days[date(2026, 9, 2)] == pytest.approx(24 * 6)


def test_a_frozen_rectifier_makes_its_day_missing(tmp_path: Path) -> None:
    """One counter stands still while the others run more than 1,000 kWh."""
    path = tmp_path / SCADA_FA_WORKBOOK
    _scada_workbook(path, _local(2026, 9, 1), 24 * 3 + 1, (0, 1_000_000, 1_000_000))

    assert read_rectifier_days(path) == {}


def test_daily_rows_join_gold_production_and_derive_sunday_and_no_production(
    tmp_path: Path,
) -> None:
    """2026-09-06 is a Sunday that produced nothing; a day outside Gold is left out."""
    _scada_workbook(
        tmp_path / SCADA_FA_WORKBOOK, _local(2026, 9, 5), 24 * 4 + 1, (1_000, 1_000, 1_000)
    )
    production = {date(2026, 9, 5): 2_000.0, date(2026, 9, 6): 0.0, date(2026, 9, 7): 1_500.0}
    source = EnergyWorkbookSource(tmp_path, lambda: production)

    rows = source.rows(SPECS[ModelKey.FA_DIARIO])

    assert [(row.period, dict(row.features)) for row in rows] == [
        (date(2026, 9, 5), {"eq": 2_000.0, "domingo": 0.0, "sin_eq": 0.0}),
        (date(2026, 9, 6), {"eq": 0.0, "domingo": 1.0, "sin_eq": 1.0}),
        (date(2026, 9, 7), {"eq": 1_500.0, "domingo": 0.0, "sin_eq": 0.0}),
    ]
    assert rows[0].target == pytest.approx(72.0)
