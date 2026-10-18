"""Tests for the daily and monthly series of the equivalent batteries, without a database.

Layer: tests (unit)

The daily view is SQL and is tested against PostgreSQL elsewhere. What is
tested here is what Python adds: which drivers and processes a series keeps,
how the filters are read and refused, the totals of each day or month, the
reconciliation of whole months, and the table, JSON and CSV the command prints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from energy.domain.equs_gold import DateRange, Driver, DriverAmount, VersionMonth
from energy.domain.equs_series import (
    SERIES_FIELDS,
    BatteryEquivalents,
    DailyDriverAmount,
    ReconciledAmount,
    SeriesRow,
    describe_series,
    period_starts,
    series_csv_rows,
    series_json,
    totals,
)
from energy.domain.exceptions import ConfigurationError, InvalidPeriodError
from energy.ports.equs_gold import DailyGoldReader
from energy.services.equs_gold import BatterySeries

if TYPE_CHECKING:
    from collections.abc import Sequence

pytestmark = pytest.mark.unit

SEPTEMBER = date(2026, 9, 1)
AUGUST = date(2026, 8, 1)
D = Decimal


def _driver(cuenta: str, proceso: str, orden: int, area: str | None, factor: str = "0.5",
            *, suma_eq: bool = True) -> Driver:  # fmt: skip
    return Driver(
        version=1, version_name="V1", cuenta_stats=cuenta, descripcion=cuenta, proceso=proceso,
        unidad="UN", fuente="PRODUCCION", estandar_por_bat=D(1), multiplicador=D(1),
        peso=D(factor), factor=D(factor), suma_eq=suma_eq, fila=None, planta=proceso,
        area_planta=area, orden=orden, peso_proceso=D(factor),
    )  # fmt: skip


DRIVERS = (
    _driver("OX", "OREC", 1, "OREC"),
    _driver("AS", "ENSAMBLE", 2, "Ensamble", "2"),
    _driver("FO", "FORMACION", 3, "F&A"),
    _driver("DE", "DECORADO", 4, "F&A"),
    _driver("SH", "SHIPPING", 5, None),
    _driver("INFO", "INFO", 6, None, "0", suma_eq=False),
)
FACTORS = {driver.cuenta_stats: driver.factor for driver in DRIVERS}


def _day(day: int, cuenta: str, cantidad: str, month: date = SEPTEMBER) -> DailyDriverAmount:
    when = month.replace(day=day)
    return DailyDriverAmount(when, month, 1, cuenta, D(cantidad), D(cantidad) * FACTORS[cuenta])


DAYS = (
    _day(1, "OX", "100"),
    _day(1, "AS", "10"),
    _day(2, "OX", "50"),
    _day(2, "FO", "40"),
    _day(2, "SH", "-4"),
    _day(3, "DE", "6"),
    _day(3, "INFO", "999"),
)


@dataclass
class FakeReader:
    """Answers the month and day questions from fixed rows."""

    versions_by_month: dict[date, list[int | None]] = field(
        default_factory=lambda: {SEPTEMBER: [1], AUGUST: [1]}
    )
    days: tuple[DailyDriverAmount, ...] = DAYS
    reconciled: tuple[ReconciledAmount, ...] = ()
    asked: list[str] = field(default_factory=list)

    def versions(self, period: DateRange) -> tuple[VersionMonth, ...]:
        return tuple(
            VersionMonth(month, version, f"V{version}" if version else None)
            for month in period.months
            for version in self.versions_by_month.get(month, [None])
        )

    def drivers(self, versions: Sequence[int]) -> tuple[Driver, ...]:
        return tuple(driver for driver in DRIVERS if driver.version in versions)

    def amounts(self, period: DateRange, *, closed: bool) -> tuple[DriverAmount, ...]:
        self.asked.append(f"amounts closed={closed}")
        sums: dict[tuple[date, str], Decimal] = {}
        for item in self.daily_amounts(period):
            sums[item.month, item.cuenta_stats] = (
                sums.get((item.month, item.cuenta_stats), D(0)) + item.cantidad
            )
        listed = [
            DriverAmount(month, 1, driver.cuenta_stats, D(0), D(0))
            for month in period.months
            for driver in DRIVERS
        ]
        return tuple(
            DriverAmount(row.month, 1, row.cuenta_stats, q, q * FACTORS[row.cuenta_stats])
            for row in listed
            for q in [sums.get((row.month, row.cuenta_stats), D(0))]
        )

    def daily_amounts(self, period: DateRange) -> tuple[DailyDriverAmount, ...]:
        self.asked.append(f"days {period.start}..{period.end}")
        return tuple(item for item in self.days if period.start <= item.day <= period.end)

    def daily_reconciliation(self, months: DateRange) -> tuple[ReconciledAmount, ...]:
        self.asked.append(f"reconcile {months.start}..{months.end}")
        return self.reconciled or (
            ReconciledAmount(SEPTEMBER, 1, "OX", D(150), D(150), D(75), D(75), equal=True),
        )


def _series(reader: FakeReader | None = None, period: DateRange | None = None,
            **options: Any) -> BatteryEquivalents:  # fmt: skip
    chosen = reader or FakeReader()
    return BatterySeries(chosen, chosen).series(period or DateRange.of_month(SEPTEMBER), **options)


def test_the_fake_honours_the_daily_port() -> None:
    """So the series is tested against the interface it gets in production."""
    assert isinstance(FakeReader(), DailyGoldReader)


# -- What a series keeps ------------------------------------------------------------
def test_a_daily_series_keeps_only_the_battery_drivers() -> None:
    """The informative driver is left out; the processes come in the order of the report."""
    series = _series()

    assert series.procesos == ("OREC", "ENSAMBLE", "FORMACION", "DECORADO", "SHIPPING")
    assert not series.filtered
    assert series.total_label == "EQUS BATT"
    assert [(row.start.day, row.cuenta_stats) for row in series.rows] == [
        (1, "OX"),
        (1, "AS"),
        (2, "OX"),
        (2, "FO"),
        (2, "SH"),
        (3, "DE"),
    ]
    shipping = series.rows[4]
    assert (shipping.area_planta, shipping.equs) == (None, D(-2))


def test_every_day_has_a_total_at_zero_when_nothing_was_produced() -> None:
    """Thirty days of September, by process and in all."""
    days = totals(_series())

    assert len(days) == 30
    assert days[0].by_process == {
        "OREC": D(50), "ENSAMBLE": D(20), "FORMACION": D(0), "DECORADO": D(0), "SHIPPING": D(0),
    }  # fmt: skip
    assert days[0].total == D(70)
    assert days[1].total == D(25) + D(20) - D(2)
    assert days[29].total == 0


def test_a_monthly_series_reads_the_months_and_drops_drivers_at_zero() -> None:
    """The same drivers, from the monthly amounts; no reconciliation is needed."""
    reader = FakeReader()
    series = _series(reader, DateRange(AUGUST, date(2026, 9, 30)), frequency="month")

    assert "amounts closed=False" in reader.asked
    assert not [asked for asked in reader.asked if asked.startswith("reconcile")]
    assert {row.start for row in series.rows} == {SEPTEMBER}
    assert {row.cuenta_stats: row.cantidad for row in series.rows} == {
        "OX": D(150), "AS": D(10), "FO": D(40), "DE": D(6), "SH": D(-4),
    }  # fmt: skip
    assert period_starts(series.period, "month") == (AUGUST, SEPTEMBER)
    assert series.reconciliation == ()


def test_a_daily_series_checks_only_whole_months() -> None:
    """From 20 August to 30 September only September is whole."""
    reader = FakeReader()
    series = _series(reader, DateRange(date(2026, 8, 20), date(2026, 9, 30)))

    assert "reconcile 2026-09-01..2026-09-30" in reader.asked
    assert series.reconciled_months == (SEPTEMBER,)
    assert series.mismatches == ()

    partial = FakeReader()
    _series(partial, DateRange(date(2026, 9, 2), date(2026, 9, 30)))
    assert not [asked for asked in partial.asked if asked.startswith("reconcile")]


# -- Filters ------------------------------------------------------------------------
def test_filters_by_process_and_by_area_in_any_case() -> None:
    """F&A is formation and decoration; an area leaves shipping out."""
    by_process = _series(procesos=["orec", "Shipping"])
    assert by_process.procesos == ("OREC", "SHIPPING")
    assert by_process.filtered
    assert by_process.total_label == "total"
    assert {row.proceso for row in by_process.rows} == {"OREC", "SHIPPING"}

    by_area = _series(areas=["f&a"])
    assert by_area.procesos == ("FORMACION", "DECORADO")

    both = _series(procesos=["FORMACION", "OREC"], areas=["F&A"])
    assert both.procesos == ("FORMACION",)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"procesos": ["INFO"]}, "not a battery process"),
        ({"procesos": ["LEADPROD"]}, "not a battery process"),
        ({"areas": ["RC"]}, "not the plant area of a battery process"),
        ({"procesos": ["SHIPPING"], "areas": ["OREC"]}, "both in the processes and in the areas"),
        ({"frequency": "week"}, "day or month"),
    ],
)
def test_filters_that_cannot_be_answered_are_refused(options: dict[str, Any], message: str) -> None:
    """Refused before anything is read, with what was wrong."""
    with pytest.raises(InvalidPeriodError, match=message):
        _series(**options)


def test_ranges_are_refused_as_the_report_refuses_them() -> None:
    """A month without a version, or two versions in one month."""
    with pytest.raises(InvalidPeriodError, match="no version") as refused:
        _series(FakeReader(versions_by_month={SEPTEMBER: [1]}), DateRange(AUGUST, SEPTEMBER))
    assert refused.value.details["months"] == ["2026-08"]

    with pytest.raises(ConfigurationError, match="two versions"):
        _series(FakeReader(versions_by_month={SEPTEMBER: [1, 2]}))


# -- Output -------------------------------------------------------------------------
def test_the_table_has_a_line_per_day_and_says_the_days_add_up() -> None:
    """A header, thirty days, the total and the reconciliation."""
    lines = describe_series(_series())

    assert lines[0] == "Equivalent batteries by day, 2026-09-01 to 2026-09-30 (live)"
    assert lines[2].split() == ["day", "OREC", "ENSAMBLE", "FORMACION", "DECORADO", "SHIPPING",
                                "EQUS", "BATT"]  # fmt: skip
    assert lines[3].split() == ["2026-09-01", "50.00", "20.00", "0.00", "0.00", "0.00", "70.00"]
    assert lines[33].split()[0] == "total"
    assert lines[33].split()[-1] == "116.00"
    assert lines[-1] == (
        "The days add up to gold.v_resumen_equ exactly: 2026-09, 1 driver-months compared."
    )


def test_a_mismatch_is_listed() -> None:
    """Each driver-month whose days do not add up, with both sides."""
    reader = FakeReader(
        reconciled=(
            ReconciledAmount(SEPTEMBER, 1, "OX", D(150), D(150), D(75), D(75), equal=True),
            ReconciledAmount(SEPTEMBER, 1, "FO", D(40), D(41), D(20), D("20.5"), equal=False),
        )
    )
    series = _series(reader)

    assert [item.cuenta_stats for item in series.mismatches] == ["FO"]
    lines = describe_series(series)
    assert "1 of 2 driver-months of 2026-09 do not add up to gold.v_resumen_equ:" in lines
    assert series_json(series)["conciliacion"]["diferencias"] == [
        {"mes": "2026-09", "version": 1, "cuenta_stats": "FO", "cantidad_mes": 40,
         "cantidad_dias": 41, "equs_mes": 20, "equs_dias": 20.5},
    ]  # fmt: skip


def test_json_and_csv_carry_the_same_rows() -> None:
    """Totals per day for a chart, and every driver row for a check."""
    series = _series(areas=["F&A"])
    payload = series_json(series)

    assert payload["frecuencia"] == "day"
    assert payload["procesos"] == ["FORMACION", "DECORADO"]
    assert payload["total"] == "total"
    assert payload["periodos"][1] == {
        "periodo": "2026-09-02",
        "procesos": {"FORMACION": 20, "DECORADO": 0},
        "total": 20,
    }
    assert payload["filas"] == [
        {"periodo": "2026-09-02", "version": 1, "proceso": "FORMACION", "area_planta": "F&A",
         "cuenta_stats": "FO", "cantidad": 40, "factor": 0.5, "equs": 20},
        {"periodo": "2026-09-03", "version": 1, "proceso": "DECORADO", "area_planta": "F&A",
         "cuenta_stats": "DE", "cantidad": 6, "factor": 0.5, "equs": 3},
    ]  # fmt: skip
    assert payload["conciliacion"]["filas_comparadas"] == 1

    rows = series_csv_rows(series)
    assert rows[0] == list(SERIES_FIELDS)
    assert rows[1] == ["2026-09-02", 1, "FORMACION", "F&A", "FO", 40, 0.5, 20]

    monthly = series_json(_series(frequency="month"))
    assert "conciliacion" not in monthly
    assert monthly["periodos"][0]["periodo"] == "2026-09"


def test_the_table_rounds_half_up_as_the_workbook_does() -> None:
    """9,015.285 is 9,015.29, as section 9.2 has July's shipping; not 9,015.28."""
    month = date(2026, 7, 1)
    series = BatteryEquivalents(
        period=DateRange.of_month(month),
        frequency="month",
        procesos=("SHIPPING",),
        filtered=True,
        versions=(VersionMonth(month, 1, "V1"),),
        rows=(
            SeriesRow(month, 1, "SHIPPING", None, "UNITSHIP", D(1), D("9015.285"), D("9015.285")),
        ),
    )

    assert describe_series(series)[3].split() == ["2026-07", "9,015.29", "9,015.29"]


# -- Drivers by day ------------------------------------------------------------------
def test_drivers_by_day_includes_the_informative_drivers() -> None:
    """The formed batteries count units but carry no EQ; series() leaves them out."""
    reader = FakeReader()

    rows = BatterySeries(reader, reader).drivers_by_day(
        DateRange.of_month(SEPTEMBER), ["INFO", "DE"]
    )

    assert [(row.start.day, row.cuenta_stats, row.proceso, row.cantidad) for row in rows] == [
        (3, "DE", "DECORADO", D(6)),
        (3, "INFO", "INFO", D(999)),
    ]
    assert rows[1].equs == 0


def test_drivers_by_day_refuses_a_code_that_is_not_a_driver() -> None:
    reader = FakeReader()

    with pytest.raises(InvalidPeriodError, match="not a driver"):
        BatterySeries(reader, reader).drivers_by_day(DateRange.of_month(SEPTEMBER), ["NOPE"])
