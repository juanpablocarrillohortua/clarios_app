"""Tests for EQUS Gold without a database: periods, the report's layout, the use cases.

Layer: tests (unit)

The calculation is SQL and is tested against PostgreSQL elsewhere. What is
tested here is what Python adds: reading periods, arranging the database's rows
the way RESUMEN EQU does, rounding, warnings, the column-H table, and the
decisions the three use cases make between the steps, with fake ports.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from energy.domain.equs_gold import (
    ClosedDifference,
    CloseSummary,
    DateRange,
    Driver,
    DriverAmount,
    FrozenMonth,
    PolyAmount,
    QualityCount,
    RefreshStep,
    ReportData,
    VersionMonth,
    column_h,
    describe_close,
    describe_quality,
    json_number,
    month_end,
    nest_report,
    parse_day,
    parse_month,
    report_warnings,
)
from energy.domain.exceptions import CloseRefusedError, ConfigurationError, InvalidPeriodError
from energy.ports.equs_gold import GoldMaintenance, GoldReader
from energy.services.equs_gold import GoldRefresher, MonthCloser, RangeReporter

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

pytestmark = pytest.mark.unit

SEPTEMBER = date(2026, 9, 1)
AUGUST = date(2026, 8, 1)
NOW = datetime(2026, 10, 7, 23, 0, tzinfo=UTC)
D = Decimal


def _driver(
    cuenta: str,
    proceso: str,
    planta: str,
    orden: int,
    fila: int | None,
    *,
    factor: str = "0.5",
    peso: str = "0.1",
    peso_proceso: str = "0.1",
    suma_eq: bool = True,
    version: int = 1,
    area: str | None = None,
) -> Driver:
    return Driver(
        version=version,
        version_name=f"V{version}",
        cuenta_stats=cuenta,
        descripcion=f"{cuenta} description",
        proceso=proceso,
        unidad="UN",
        fuente="PRODUCCION",
        estandar_por_bat=D(1),
        multiplicador=D(factor) / D(peso) if D(peso) else D(1),
        peso=D(peso),
        factor=D(factor),
        suma_eq=suma_eq,
        fila=fila,
        planta=planta,
        area_planta=area,
        orden=orden,
        peso_proceso=D(peso_proceso),
    )


DRIVERS = (
    _driver("OX", "OREC", "OREC", 1, 2, area="OREC", peso_proceso="0.3"),
    _driver("PL", "OREC", "OREC", 1, 6, area="OREC", peso_proceso="0.3"),
    _driver("FO", "FORMACION", "F&A", 3, 10, area="F&A", peso_proceso="0.2"),
    _driver("DE", "DECORADO", "F&A", 4, 14, area="F&A", peso_proceso="0.1"),
    _driver("SH", "SHIPPING", "DESPACHOS", 5, 16, peso_proceso="0.05"),
    _driver("INFO1", "INFO", "INFORMATIVOS", 6, 12, factor="0", peso="0", suma_eq=False),
)


def _amounts(month: date, values: dict[str, str], version: int = 1) -> tuple[DriverAmount, ...]:
    factors = {driver.cuenta_stats: driver.factor for driver in DRIVERS}
    return tuple(
        DriverAmount(month, version, cuenta, D(cantidad), D(cantidad) * factors[cuenta])
        for cuenta, cantidad in values.items()
    )


POLY = (
    PolyAmount(SEPTEMBER, "CONTPROD_CAR", "CAJA SLI", "Componentes", D(200), D(200), D(1),
               D(200), 20),
    PolyAmount(SEPTEMBER, "NA", "PELLET", "Componentes", D(300), D(0), D(0), D(0), None),
    PolyAmount(SEPTEMBER, "COVRPROD_SLI1", "CUB 1P", "Componentes", D(1000), D(1000), D("0.51"),
               D(510), 23),
)  # fmt: skip

QUALITY = (
    QualityCount("dq_sin_regla", "no rule", bloqueante=True, filas=0),
    QualityCount("dq_categoria_poly", "category", bloqueante=False, filas=3),
)


def _data(**changes: Any) -> ReportData:
    data = ReportData(
        period=DateRange(SEPTEMBER, date(2026, 9, 30)),
        source="live",
        generated_at=NOW,
        versions=(VersionMonth(SEPTEMBER, 1, "V1"),),
        drivers=DRIVERS,
        amounts=_amounts(
            SEPTEMBER, {"OX": "10", "PL": "4", "FO": "2", "DE": "0", "SH": "6", "INFO1": "9"}
        ),
        poly=POLY,
        quality=QUALITY,
    )
    for key, value in changes.items():
        object.__setattr__(data, key, value)
    return data


# =============================================================================
# Periods
# =============================================================================
def test_a_month_is_written_year_dash_month() -> None:
    """The first day of the month; anything else is refused."""
    assert parse_month("2026-09") == SEPTEMBER
    assert parse_month(" 2026-12 ") == date(2026, 12, 1)
    for text in ("2026-13", "2026-9", "09-2026", "2026-09-01", ""):
        with pytest.raises(InvalidPeriodError):
            parse_month(text)


def test_a_day_is_written_iso() -> None:
    """Calendar dates only, named in the error."""
    assert parse_day("2026-02-28") == date(2026, 2, 28)
    with pytest.raises(InvalidPeriodError, match="--end is written"):
        parse_day("2026-02-30", name="--end")
    with pytest.raises(InvalidPeriodError):
        parse_day("2026/02/01")


def test_a_range_knows_its_months() -> None:
    """Both ends included; a month touched but not covered is partial."""
    period = DateRange(date(2026, 3, 15), date(2026, 5, 31))
    assert period.days == 78
    assert period.months == (date(2026, 3, 1), date(2026, 4, 1), date(2026, 5, 1))
    assert period.partial_months == (date(2026, 3, 1),)
    assert DateRange.of_month(date(2024, 2, 10)) == DateRange(date(2024, 2, 1), date(2024, 2, 29))
    assert month_end(date(2026, 12, 5)) == date(2026, 12, 31)
    with pytest.raises(InvalidPeriodError, match="later than the end"):
        DateRange(date(2026, 9, 2), date(2026, 9, 1))


def test_json_numbers_are_rounded_half_up_and_integral_ones_are_integers() -> None:
    """As the workbook rounds, and as it shows whole quantities."""
    assert json_number(D("1162129.000000"), 6) == 1162129
    assert isinstance(json_number(D("1162129.000000"), 6), int)
    assert json_number(D("0.0000005"), 6) == 0.000001
    assert json_number(D("9015.285"), 2) == 9015.29
    assert json_number(D("0.012171052631578947"), 10) == 0.0121710526
    assert json_number(None, 6) is None


# =============================================================================
# The report
# =============================================================================
def test_the_report_is_laid_out_as_resumen_equ() -> None:
    """Plants in order, processes in order, drivers by their row; informative apart."""
    report = nest_report(_data())

    assert [plant["planta"] for plant in report["plantas"]] == ["OREC", "F&A", "DESPACHOS"]
    fya = report["plantas"][1]
    assert [process["proceso"] for process in fya["procesos"]] == ["FORMACION", "DECORADO"]
    assert fya["peso"] == pytest.approx(0.3)
    assert [
        item["cuenta_stats"] for item in report["plantas"][0]["procesos"][0]["subsecciones"]
    ] == [
        "OX",
        "PL",
    ]
    assert report["informativos"] == [
        {"fila": 12, "cuenta_stats": "INFO1", "descripcion": "INFO1 description", "unidad": "UN",
         "cantidad": 9},
    ]  # fmt: skip
    assert report["equs_batt"] == 11  # (10 + 4 + 2 + 0 + 6) x 0.5
    assert report["versiones"] == [{"version": 1, "nombre": "V1", "meses": ["2026-09"]}]
    assert report["rango"] == {
        "desde": "2026-09-01",
        "hasta": "2026-09-30",
        "dias": 30,
        "meses": ["2026-09"],
    }
    assert report["calidad_datos"] == {"dq_sin_regla": 0, "dq_categoria_poly": 3}
    assert report["advertencias"] == []
    assert report["generado_en"] == "2026-10-07T23:00:00+00:00"


def test_every_driver_appears_even_without_an_amount() -> None:
    """A driver the database returned no amount for is shown with 0."""
    report = nest_report(_data(amounts=_amounts(SEPTEMBER, {"OX": "1"})))
    shipping = report["plantas"][2]["procesos"][0]["subsecciones"][0]
    assert (shipping["cuenta_stats"], shipping["cantidad"], shipping["equs"]) == ("SH", 0, 0)


def test_totals_are_added_before_rounding() -> None:
    """Three amounts that each round to nothing still add up to something."""
    tiny = tuple(
        DriverAmount(SEPTEMBER, 1, cuenta, D(0), D("0.0000004")) for cuenta in ("OX", "PL", "FO")
    )
    report = nest_report(_data(amounts=tiny))
    assert report["plantas"][0]["procesos"][0]["subsecciones"][0]["equs"] == 0
    assert report["equs_batt"] == 0.000001


def test_equs_poly_lists_every_category_and_the_pieces_that_carry_eq() -> None:
    """In the order of RESUMEN EQU, the pellet last; D32 leaves it out."""
    poly = nest_report(_data())["equs_poly"]
    assert [item["categoria"] for item in poly["subsecciones"]] == ["CAJA SLI", "CUB 1P", "PELLET"]
    assert (poly["piezas"], poly["piezas_con_equ"], poly["equs"]) == (1500, 1200, 710)
    assert poly["area_planta"] == "Componentes"


def test_a_range_across_versions_gives_each_factor() -> None:
    """The factor is not one number: null, and one entry per version."""
    v2 = tuple(
        _driver(
            d.cuenta_stats,
            d.proceso,
            d.planta,
            d.orden,
            d.fila,
            factor="1",
            peso=str(d.peso),
            peso_proceso=str(d.peso_proceso),
            suma_eq=d.suma_eq,
            version=2,
            area=d.area_planta,
        )
        for d in DRIVERS
    )
    amounts = (*_amounts(AUGUST, {"OX": "10"}), DriverAmount(SEPTEMBER, 2, "OX", D(4), D(4)))
    data = _data(
        period=DateRange(AUGUST, date(2026, 9, 30)),
        versions=(VersionMonth(AUGUST, 1, "V1"), VersionMonth(SEPTEMBER, 2, "V2")),
        drivers=DRIVERS + v2,
        amounts=amounts,
        by_month=True,
    )
    report = nest_report(data)
    oxide = report["plantas"][0]["procesos"][0]["subsecciones"][0]
    assert oxide["factor"] is None
    assert oxide["por_version"] == [
        {"version": 1, "cantidad": 10, "estandar_por_bat": 1, "multiplicador": 5, "peso": 0.1,
         "factor": 0.5, "equs": 5},
        {"version": 2, "cantidad": 4, "estandar_por_bat": 1, "multiplicador": 10, "peso": 0.1,
         "factor": 1, "equs": 4},
    ]  # fmt: skip
    assert oxide["equs"] == 9
    assert oxide["por_mes"] == [
        {"mes": "2026-08", "cantidad": 10, "equs": 5},
        {"mes": "2026-09", "cantidad": 4, "equs": 4},
    ]
    assert report["por_mes"] == [
        {"mes": "2026-08", "equs_batt": 5, "equs_poly": 0},
        {"mes": "2026-09", "equs_batt": 4, "equs_poly": 710},
    ]
    assert [w.codigo for w in report_warnings(data)] == ["varias_versiones"]


def test_warnings_say_what_a_reader_must_know() -> None:
    """A cut month, blocking checks, a close the live data left, a frozen source."""
    data = _data(
        period=DateRange(date(2026, 9, 16), date(2026, 9, 30)),
        quality=(QualityCount("dq_sin_regla", "no rule", bloqueante=True, filas=2),),
        closed_differences=(ClosedDifference(SEPTEMBER, "OX", D(5), D(4)),),
        source="closed",
    )
    warnings = {warning.codigo: warning for warning in report_warnings(data)}
    assert list(warnings) == [
        "mes_parcial",
        "calidad_bloqueante",
        "difiere_del_cierre",
        "poly_en_vivo",
    ]
    assert warnings["mes_parcial"].meses == ("2026-09",)
    assert "dq_sin_regla (2)" in warnings["calidad_bloqueante"].mensaje
    assert "OX" in warnings["difiere_del_cierre"].mensaje
    assert warnings["poly_en_vivo"].as_dict() == {
        "codigo": "poly_en_vivo",
        "mensaje": warnings["poly_en_vivo"].mensaje,
    }


def test_column_h_flattens_the_report_in_the_workbooks_order() -> None:
    """Drivers by row, then the totals of rows 36 to 45, then EQUS POLY."""
    rows = column_h(nest_report(_data()))
    assert [(row.tipo, row.nombre) for row in rows] == [
        ("cuenta", "OX"),
        ("cuenta", "PL"),
        ("cuenta", "FO"),
        ("cuenta", "INFO1"),
        ("cuenta", "DE"),
        ("cuenta", "SH"),
        ("total", "EQUS BATT"),
        ("total", "OREC"),
        ("total", "F&A"),
        ("total", "DESPACHOS"),
        ("total", "FORMACION"),
        ("total", "DECORADO"),
        ("poly", "CONTPROD_CAR"),
        ("poly", "COVRPROD_SLI1"),
        ("poly", "NA"),
        ("total", "EQUS POLY"),
    ]
    by_name = {row.nombre: row for row in rows}
    assert (by_name["OX"].cantidad, by_name["OX"].factor, by_name["OX"].equs) == (10, 0.5, 5)
    assert by_name["INFO1"].equs is None
    assert by_name["EQUS POLY"].cantidad == 1200
    assert by_name["CONTPROD_CAR"].categoria == "CAJA SLI"
    assert set(by_name["OX"].as_dict()) >= {"tipo", "fila", "nombre", "equs"}


def test_the_close_reads_as_text() -> None:
    """The month's EQ by process with its change against the previous close."""
    summary = CloseSummary(
        frozen=FrozenMonth(SEPTEMBER, 7, 13, reclosed=True, replaced=12, accepted_warnings=True),
        quality=QUALITY,
        processes=(("OREC", D(110)), ("SHIPPING", D(0))),
        equs_batt=D(110),
        equs_poly=D("503033.19"),
        previous_month=AUGUST,
        previous_processes=(("OREC", D(100)),),
        previous_equs_batt=D(100),
    )
    text = "\n".join(describe_close(summary))
    assert "Reclosed 2026-09: 13 driver rows frozen (close 7)." in text
    assert "The 12 rows it replaced are kept in the history." in text
    assert "the warnings were accepted" in text
    assert "previous closed month 2026-08" in text
    assert "(+10.0 % against 100.00)" in text
    assert "503,033.19   (live, not frozen)" in text

    lines = describe_quality((QualityCount("dq_sin_regla", "no rule", bloqueante=True, filas=4),))
    assert "BLOCKING" in lines[1]


# =============================================================================
# The use cases, with fake ports
# =============================================================================
@dataclass
class FakeReader:
    """Answers from fixed rows."""

    versions_by_month: dict[date, list[int | None]] = field(
        default_factory=lambda: {SEPTEMBER: [1], AUGUST: [1]}
    )
    closed: tuple[date, ...] = ()
    quality_rows: tuple[QualityCount, ...] = QUALITY
    live: tuple[DriverAmount, ...] = field(
        default_factory=lambda: _amounts(SEPTEMBER, {"OX": "10"})
    )
    frozen: tuple[DriverAmount, ...] = field(
        default_factory=lambda: _amounts(SEPTEMBER, {"OX": "10"})
    )
    asked: list[str] = field(default_factory=list)

    def versions(self, period: DateRange) -> tuple[VersionMonth, ...]:
        return tuple(
            VersionMonth(month, version, f"V{version}" if version else None)
            for month in period.months
            for version in self.versions_by_month.get(month, [None])
        )

    def drivers(self, versions: Sequence[int]) -> tuple[Driver, ...]:
        self.asked.append(f"drivers {list(versions)}")
        return tuple(driver for driver in DRIVERS if driver.version in versions)

    def amounts(self, period: DateRange, *, closed: bool) -> tuple[DriverAmount, ...]:
        self.asked.append(f"amounts closed={closed}")
        rows = self.frozen if closed else self.live
        return tuple(row for row in rows if period.start <= row.month <= period.end)

    def poly(self, _period: DateRange) -> tuple[PolyAmount, ...]:
        return POLY

    def quality(self, _period: DateRange) -> tuple[QualityCount, ...]:
        return self.quality_rows

    def closed_months(self, period: DateRange) -> tuple[date, ...]:
        return tuple(month for month in self.closed if month in period.months)

    def closed_processes(self, month: date) -> tuple[tuple[str, Decimal], ...]:
        return (("OREC", D(5)),) if month in self.closed else ()

    def previous_closed_month(self, month: date) -> date | None:
        earlier = [closed for closed in self.closed if closed < month]
        return max(earlier) if earlier else None

    def latest_production_month(self) -> date | None:
        return SEPTEMBER


@dataclass
class FakeMaintenance:
    """Records the steps it is asked for."""

    steps: list[str] = field(default_factory=list)
    frozen_with: dict[str, Any] = field(default_factory=dict)

    @contextmanager
    def locked(self, timeout_seconds: float) -> Iterator[None]:
        self.steps.append(f"lock {timeout_seconds}")
        try:
            yield
        finally:
            self.steps.append("unlock")

    def refresh_views(self, *, concurrently: bool = True) -> tuple[RefreshStep, ...]:
        self.steps.append(f"refresh concurrently={concurrently}")
        return (
            RefreshStep("refresh silver.td_form", 0.1),
            RefreshStep("refresh silver.td_deco", 0.1),
        )

    def analyze(self) -> RefreshStep:
        self.steps.append("analyze")
        return RefreshStep("analyze", 0.2)

    def freeze_month(self, month: date, **options: Any) -> FrozenMonth:
        self.steps.append("freeze")
        self.frozen_with = options
        return FrozenMonth(month, 1, 13, reclosed=options["reclose"], replaced=0,
                           accepted_warnings=options["accepted_warnings"])  # fmt: skip


def test_the_fakes_honour_the_ports() -> None:
    """So the use cases are tested against the interface they get in production."""
    assert isinstance(FakeReader(), GoldReader)
    assert isinstance(FakeMaintenance(), GoldMaintenance)


def test_a_report_refuses_months_without_a_version() -> None:
    """No version of the equation covers August 2025."""
    reader = FakeReader(versions_by_month={SEPTEMBER: [1]})
    with pytest.raises(InvalidPeriodError) as refused:
        RangeReporter(reader).report(DateRange(AUGUST, date(2026, 9, 30)), generated_at=NOW)
    assert refused.value.details["months"] == ["2026-08"]


def test_a_report_refuses_overlapping_versions() -> None:
    """Two versions in force in one month would count it twice."""
    reader = FakeReader(versions_by_month={SEPTEMBER: [1, 2]})
    with pytest.raises(ConfigurationError, match="two versions"):
        RangeReporter(reader).report(DateRange.of_month(SEPTEMBER), generated_at=NOW)


def test_the_closed_source_needs_whole_closed_months() -> None:
    """Partial and unclosed months are named."""
    reader = FakeReader(closed=(SEPTEMBER,))
    with pytest.raises(InvalidPeriodError) as refused:
        RangeReporter(reader).report(
            DateRange(date(2026, 8, 1), date(2026, 9, 15)), source="closed", generated_at=NOW
        )
    assert refused.value.details == {
        "partial_months": ["2026-09"],
        "months_not_closed": ["2026-08"],
    }


def test_the_closed_source_reads_the_frozen_rows() -> None:
    """Versions come from the close; EQUS POLY is computed and flagged."""
    reader = FakeReader(closed=(SEPTEMBER,), frozen=_amounts(SEPTEMBER, {"OX": "3"}))
    data = RangeReporter(reader).report(
        DateRange.of_month(SEPTEMBER), source="closed", generated_at=NOW
    )
    assert "amounts closed=True" in reader.asked
    assert [a.cantidad for a in data.amounts] == [3]
    assert data.versions == (VersionMonth(SEPTEMBER, 1, "V1"),)
    assert [w.codigo for w in report_warnings(data)] == ["poly_en_vivo"]


def test_a_live_report_compares_closed_months_with_their_close() -> None:
    """A driver that moved more than a cent since the close is reported."""
    reader = FakeReader(
        closed=(SEPTEMBER,),
        live=_amounts(SEPTEMBER, {"OX": "10", "PL": "4"}),
        frozen=_amounts(SEPTEMBER, {"OX": "10"}),
    )
    data = RangeReporter(reader).report(DateRange.of_month(SEPTEMBER), generated_at=NOW)
    assert data.closed_differences == (ClosedDifference(SEPTEMBER, "PL", D(2), D(0)),)


def test_a_report_refuses_an_unknown_source() -> None:
    """Only live and closed exist."""
    with pytest.raises(InvalidPeriodError, match="live or closed"):
        RangeReporter(FakeReader()).report(
            DateRange.of_month(SEPTEMBER),
            source="frozen",  # type: ignore[arg-type]
            generated_at=NOW,
        )


def test_a_close_with_blocking_rows_stops_before_freezing() -> None:
    """The counts are shown, the close is refused, nothing is written."""
    blocking = (QualityCount("dq_sin_regla", "no rule", bloqueante=True, filas=2),)
    maintenance = FakeMaintenance()
    shown: list[Sequence[QualityCount]] = []
    closer = MonthCloser(maintenance, FakeReader(quality_rows=blocking))

    with pytest.raises(CloseRefusedError) as refused:
        closer.close(SEPTEMBER, closed_by="x", lock_timeout=5, on_quality=shown.append)

    assert refused.value.details["checks"] == {"dq_sin_regla": 2}
    assert shown == [blocking]
    assert "freeze" not in maintenance.steps
    assert maintenance.steps[-1] == "unlock"


def test_accepted_warnings_are_recorded_with_the_close() -> None:
    """The decision travels with the freeze."""
    blocking = (QualityCount("dq_sin_regla", "no rule", bloqueante=True, filas=2),)
    maintenance = FakeMaintenance()
    reader = FakeReader(quality_rows=blocking, closed=(AUGUST, SEPTEMBER))
    summary = MonthCloser(maintenance, reader).close(
        SEPTEMBER, closed_by="x", accept_warnings=True, reclose=True, lock_timeout=5
    )
    assert maintenance.steps == ["lock 5", "refresh concurrently=True", "freeze", "unlock"]
    assert maintenance.frozen_with["accepted_warnings"] is True
    assert maintenance.frozen_with["reclose"] is True
    assert summary.previous_month == AUGUST
    assert summary.equs_batt == 5
    assert summary.equs_poly == 710


def test_a_close_needs_exactly_one_version() -> None:
    """None, or two, refuse the close."""
    with pytest.raises(InvalidPeriodError):
        MonthCloser(FakeMaintenance(), FakeReader(versions_by_month={})).close(
            SEPTEMBER, closed_by="x", lock_timeout=5
        )
    with pytest.raises(CloseRefusedError, match="two versions"):
        MonthCloser(FakeMaintenance(), FakeReader(versions_by_month={SEPTEMBER: [1, 2]})).close(
            SEPTEMBER, closed_by="x", lock_timeout=5
        )


@dataclass
class FakeSilver:
    """A Silver build that only counts how often it ran."""

    builds: int = 0

    def build(self) -> Any:
        self.builds += 1
        return "silver report"


def test_a_refresh_runs_its_steps_under_the_lock() -> None:
    """Silver first when asked, then the views, then the statistics."""
    maintenance = FakeMaintenance()
    silver = FakeSilver()
    outcome = GoldRefresher(maintenance, silver).refresh(
        with_silver=True, concurrently=False, lock_timeout=9
    )
    assert maintenance.steps == ["lock 9", "refresh concurrently=False", "analyze", "unlock"]
    assert silver.builds == 1
    assert outcome.silver_report == "silver report"
    assert [step.name for step in outcome.steps] == [
        "take the refresh lock",
        "build Silver from Bronze",
        "refresh silver.td_form",
        "refresh silver.td_deco",
        "analyze",
    ]


def test_a_refresh_with_silver_needs_a_silver_build() -> None:
    """Asking for one that is not wired is a configuration error, before the lock."""
    maintenance = FakeMaintenance()
    with pytest.raises(ConfigurationError):
        GoldRefresher(maintenance).refresh(with_silver=True, lock_timeout=1)
    assert maintenance.steps == []
