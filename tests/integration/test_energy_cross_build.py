"""The energy-EQ cross on the real exports: the acceptance tests of the cross tool.

Layer: tests (integration)

Each part is skipped when its files are absent (they are not in the
repository):

* the SCADA export of F&A alone, with no database: exactly one frozen counter
  (rectifier 2, 2026-07-18 to 07-22, then a jump of 33,955.84 kWh), no dip, the
  days it leaves without kWh, and the energy of each month, to the cent;
* ``Base_de_datos.xlsx``: the months of each area, and the July 2026 check of
  F&A against SCADA (SCADA 2.1 % below, per day);
* the RC export: what the main meter EMCALI measures. Neither reading fits:
  it is far more than RC, and far less than the whole site;
* the cross against the EQ of a database built from the six workbooks of
  ``data/raw_excels``, as the Gold acceptance tests build it, which pins the
  figures of ``notebooks/eda_cruce_equ_energia.ipynb``:

  ====================================  ======  ==============================
  F&A, 2026-07-02 to 2026-09-30, daily  20.90   SCADA
  F&A, 2025-10 to 2026-07, monthly      20.83   Base_de_datos only
  F&A, FY26, monthly                    20.84   Base_de_datos + SCADA Aug-Sep
  OREC, FY26 (Oct to Jun with energy)   6.68    Base_de_datos
  ENSAMBLE, FY26 (Oct to Jul)           3.07    Base_de_datos
  ====================================  ======  ==============================

The frozen counter does not move 20.90: the range's kWh is the counter
difference between its first and last midnight, which a frozen counter does not
change; it only leaves its five days without kWh.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.adapters.local_storage import LocalObjectStorage
from energy.adapters.postgres_bronze_repo import PostgresBronzeLandingRepository
from energy.domain.energy_cross import meter_months
from energy.domain.equs_gold import DateRange
from energy.pipelines.flows.energy_cross import (
    ENERGY_BOOK,
    FA_AREA,
    FA_WORKBOOK,
    RC_BUILDING_METER,
    RC_MAIN_METER,
    RC_PLANT_METERS,
    RC_WORKBOOK,
    SCADA_ZONE,
    build_cross,
    energy_sources,
    rc_sources,
)
from energy.pipelines.flows.equs_bronze_load import ROUTES, find_workbook, load_all
from energy.pipelines.flows.equs_gold import refresh_gold
from energy.services.bronze_loader import BronzeLoader

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from energy.adapters.energy_book import EnergyBookSource
    from energy.adapters.scada_workbook import ScadaCounterSource

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
EXCELS_DIR = DATA_DIR / "raw_excels"

_NO_SCADA = not (DATA_DIR / FA_WORKBOOK).is_file()
_NO_BOOK = not (DATA_DIR / ENERGY_BOOK).is_file()
_NO_RC = not (DATA_DIR / RC_WORKBOOK).is_file()
_MISSING_WORKBOOKS = [
    route.filename
    for route in ROUTES
    if not route.optional and find_workbook(EXCELS_DIR, route.filename) is None
]
# The documented figures belong to six named workbooks, so exports of other
# periods that may sit beside them are left out.
EXACT_ROUTES = tuple(replace(route, many=False) for route in ROUTES)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_NO_SCADA, reason=f"SCADA export not available: data/{FA_WORKBOOK}"),
]

CENT = Decimal("0.01")
RANGE = DateRange(date(2026, 7, 2), date(2026, 9, 30))
FY26 = DateRange(date(2025, 10, 1), date(2026, 9, 30))
BOOK_ONLY = DateRange(date(2025, 10, 1), date(2026, 7, 31))
FROZEN_DAYS = tuple(date(2026, 7, 18) + timedelta(days=offset) for offset in range(5))


def _cents(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(CENT, rounding=ROUND_HALF_UP)


def _source() -> ScadaCounterSource:
    return energy_sources(DATA_DIR)[0]  # type: ignore[return-value]


def _book() -> EnergyBookSource:
    return energy_sources(DATA_DIR)[1]  # type: ignore[return-value]


class TestScadaExport:
    """The F&A export on its own: frozen counters, days and months."""

    def test_rectifier_2_froze_once_and_nothing_else_did(self) -> None:
        """The weekend stops of rectifiers 1 and 2 are real stops, not frozen counters."""
        counters = _source().counters(FA_AREA)

        (run,) = counters.frozen
        assert run.meter == "RECTIFICADOR 2"
        assert run.start.astimezone(SCADA_ZONE).date() == date(2026, 7, 18)
        assert run.end.astimezone(SCADA_ZONE).date() == date(2026, 7, 22)
        assert run.hours == Decimal(96)
        assert _cents(run.jump_kwh) == Decimal("33955.84")
        assert {meter: round(limit or 0) for meter, limit in counters.limits.items()} == {
            "RECTIFICADOR 1": 2508,
            "RECTIFICADOR 2": 2466,
            "RECTIFICADOR 3": 1360,
        }

    def test_the_rectifiers_have_no_dip_so_the_dip_rule_moves_nothing(self) -> None:
        """Every interval is ok but the frozen run and its jump, as before the rule."""
        counters = _source().counters(FA_AREA)

        states = {item.state for meter in counters.meters for item in counters.intervals[meter]}

        assert states == {"ok", "frozen", "jump"}

    def test_the_frozen_days_have_no_kwh_and_the_others_do(self) -> None:
        energy = _source().energy(FA_AREA, RANGE)

        missing = tuple(span.start for span in energy.days if span.kwh is None)
        assert missing == FROZEN_DAYS
        assert energy.total.measured_days == RANGE.days - len(FROZEN_DAYS)
        assert all(span.kwh is not None and span.kwh > 0 for span in energy.days[:16])

    def test_each_month_is_its_counter_difference_to_the_cent(self) -> None:
        """The same kWh as the notebook, which spread the jump over the frozen hours."""
        energy = _source().energy(FA_AREA, RANGE)

        assert [(span.start, _cents(span.kwh)) for span in energy.months] == [
            (date(2026, 7, 2), Decimal("1084695.90")),
            (date(2026, 8, 1), Decimal("870272.69")),
            (date(2026, 9, 1), Decimal("1083093.03")),
        ]
        assert _cents(energy.total.kwh) == Decimal("3038061.61")

    def test_the_first_day_of_the_export_is_not_whole(self) -> None:
        """The first reading is at 19:24 on 2026-07-01, so that day has no kWh."""
        energy = _source().energy(FA_AREA, DateRange(date(2026, 7, 1), date(2026, 7, 1)))
        counters = _source().counters(FA_AREA)

        assert energy.days[0].kwh is None
        assert counters.first_read == datetime(2026, 7, 2, 0, 24, 49, 746000, tzinfo=UTC)


@pytest.mark.skipif(_NO_BOOK, reason=f"energy workbook not available: data/{ENERGY_BOOK}")
class TestEnergyBook:
    """Base_de_datos.xlsx on its own, and against the F&A export."""

    def test_each_area_has_every_month_up_to_its_last(self) -> None:
        book = _book()

        last = {area: max(book.months(area)) for area in book.areas}

        assert last == {
            "OREC": date(2026, 6, 1),
            "Ensamble": date(2026, 7, 1),
            FA_AREA: date(2026, 7, 1),
        }
        # OREC labels its Decembers with the next year; read right, they still follow.
        assert book.months("OREC")[date(2025, 12, 1)] == Decimal(336474)

    def test_july_2026_scada_is_2_1_percent_below_the_workbook_per_day(self) -> None:
        """SCADA misses 1 July (its first reading is at 19:24), so compare per day."""
        scada = _source().energy(FA_AREA, DateRange(date(2026, 7, 2), date(2026, 7, 31)))
        book = _book().months(FA_AREA)[date(2026, 7, 1)]

        assert book is not None
        assert scada.total.kwh is not None
        per_day = (scada.total.kwh / 30) / (book / 31) - 1
        assert _cents(scada.total.kwh) == Decimal("1084695.90")
        assert book == Decimal(1144675)
        assert round(per_day * 100, 1) == Decimal("-2.1")


@pytest.mark.skipif(_NO_RC or _NO_BOOK, reason="RC export or energy workbook not available")
class TestEmcali:
    """What the main meter of the RC export measures: neither reading fits."""

    APRIL_TO_SEPTEMBER = DateRange(date(2026, 4, 1), date(2026, 9, 30))

    def _months(self) -> dict[str, list[Decimal]]:
        scada, _ = rc_sources(DATA_DIR)
        months = meter_months(scada.counters("RC"), self.APRIL_TO_SEPTEMBER)
        result: dict[str, list[Decimal]] = {}
        for meter, spans in months.items():
            values = [span.kwh for span in spans]
            assert all(value is not None for value in values), meter
            result[meter] = [value for value in values if value is not None]
        return result

    def test_emcali_counts_kwh_from_march_2026(self) -> None:
        months = self._months()

        assert [round(value) for value in months[RC_MAIN_METER]] == [
            1175411, 1290890, 1620630, 1583276, 1770786, 1817875,
        ]  # fmt: skip

    def test_emcali_is_not_rc_alone(self) -> None:
        """Reading (a): EMCALI is 4 to 6 times the RC meters, every month."""
        months = self._months()

        for index, emcali in enumerate(months[RC_MAIN_METER]):
            rc = sum(months[meter][index] for meter in RC_PLANT_METERS)
            assert emcali > 4 * rc

    def test_emcali_is_not_the_whole_site(self) -> None:
        """Reading (b): what EMCALI leaves after RC, F&A and the building is far
        below OREC + Ensamble, April to June (the months the workbook has all of)."""
        months = self._months()
        book = _book()
        for index, month in enumerate((date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1))):
            rc = sum(months[meter][index] for meter in RC_PLANT_METERS)
            fa = book.months(FA_AREA)[month]
            orec = book.months("OREC")[month]
            ensamble = book.months("Ensamble")[month]
            assert fa is not None
            assert orec is not None
            assert ensamble is not None
            rest = months[RC_MAIN_METER][index] - rc - fa - months[RC_BUILDING_METER][index]
            assert rest < (orec + ensamble) / 2


@dataclass(frozen=True)
class Built:
    """The database the class works on."""

    engine: Engine


@pytest.mark.skipif(
    bool(_MISSING_WORKBOOKS), reason=f"source workbooks not available: {_MISSING_WORKBOOKS}"
)
class TestCrossAgainstTheDatabase:
    """The cross against the EQ of a database built from data/raw_excels."""

    @pytest.fixture(scope="class")
    def built(self, gold_engine: Engine, tmp_path_factory: pytest.TempPathFactory) -> Built:
        """Land the six workbooks, build Silver and refresh Gold."""
        loader = BronzeLoader(
            reader=CalamineWorkbookReader(),
            storage=LocalObjectStorage(tmp_path_factory.mktemp("cross_archive")),
            repository=PostgresBronzeLandingRepository(gold_engine),
            bucket="bronze-raw",
        )
        summary = load_all(loader, EXCELS_DIR, routes=EXACT_ROUTES)
        assert not [outcome.error for outcome in summary.outcomes if outcome.error]
        refresh_gold(with_silver=True, lock_timeout=60, engine=gold_engine)
        return Built(gold_engine)

    def test_fa_from_july_2_to_september_30_is_20_90_kwh_per_eq(self, built: Built) -> None:
        """Σ kWh of the three rectifiers / Σ EQ of FORMACION + DECORADO, same days."""
        series = build_cross(data_dir=DATA_DIR, engine=built.engine).cross(RANGE)

        (area,) = series.areas
        assert area.area == FA_AREA
        assert area.procesos == ("FORMACION", "DECORADO")
        assert _cents(area.total.kwh_per_eq) == Decimal("20.90")
        assert _cents(area.total.eq) == Decimal("145350.69")
        assert series.eq_mismatches == 0
        assert area.reconciled_months == (date(2026, 8, 1), date(2026, 9, 1))

    def test_each_month_against_its_eq(self, built: Built) -> None:
        series = build_cross(data_dir=DATA_DIR, engine=built.engine).cross(
            RANGE, frequency="month", areas=[FA_AREA]
        )

        (area,) = series.areas
        assert [(row.start, _cents(row.eq), _cents(row.kwh_per_eq)) for row in area.rows] == [
            (date(2026, 7, 2), Decimal("51801.20"), Decimal("20.94")),
            (date(2026, 8, 1), Decimal("41993.63"), Decimal("20.72")),
            (date(2026, 9, 1), Decimal("51555.86"), Decimal("21.01")),
        ]

    @pytest.mark.skipif(_NO_BOOK, reason=f"energy workbook not available: data/{ENERGY_BOOK}")
    def test_fa_with_the_workbook_alone_is_20_83(self, built: Built) -> None:
        """October 2025 to July 2026: the months of Base_de_datos, none SCADA's whole."""
        series = build_cross(data_dir=DATA_DIR, engine=built.engine).cross(
            BOOK_ONLY, frequency="month", areas=[FA_AREA]
        )

        (area,) = series.areas
        assert {row.energy_source for row in area.rows} == {ENERGY_BOOK}
        assert _cents(area.total.kwh_per_eq) == Decimal("20.83")
        assert area.total.energy_source == ENERGY_BOOK

    @pytest.mark.skipif(_NO_BOOK, reason=f"energy workbook not available: data/{ENERGY_BOOK}")
    def test_fa_over_fy26_is_20_84_with_scada_for_august_and_september(self, built: Built) -> None:
        series = build_cross(data_dir=DATA_DIR, engine=built.engine).cross(
            FY26, frequency="month", areas=[FA_AREA]
        )

        (area,) = series.areas
        sources = [row.energy_source for row in area.rows]
        assert sources == [ENERGY_BOOK] * 10 + [FA_WORKBOOK] * 2
        assert _cents(area.total.kwh) == Decimal("11433291.72")
        assert _cents(area.total.kwh_per_eq) == Decimal("20.84")
        assert area.eq_without_energy == 0

    @pytest.mark.skipif(_NO_BOOK, reason=f"energy workbook not available: data/{ENERGY_BOOK}")
    @pytest.mark.parametrize(
        ("area_name", "ratio", "months_with_energy"),
        [("OREC", "6.68", 9), ("Ensamble", "3.07", 10)],
    )
    def test_orec_and_ensamble_over_fy26(
        self, built: Built, area_name: str, ratio: str, months_with_energy: int
    ) -> None:
        """Only the months with energy count; the EQ of the others is reported apart."""
        series = build_cross(data_dir=DATA_DIR, engine=built.engine).cross(
            FY26, frequency="month", areas=[area_name]
        )

        (area,) = series.areas
        assert sum(1 for row in area.rows if row.kwh is not None) == months_with_energy
        assert _cents(area.total.kwh_per_eq) == Decimal(ratio)
        assert area.eq_without_energy > 0

    @pytest.mark.skipif(_NO_BOOK, reason=f"energy workbook not available: data/{ENERGY_BOOK}")
    def test_a_daily_cross_of_every_area_is_f_and_a_alone(self, built: Built) -> None:
        series = build_cross(data_dir=DATA_DIR, engine=built.engine).cross(RANGE)

        assert [area.area for area in series.areas] == [FA_AREA]
        assert series.monthly_only == ("OREC", "Ensamble")
        assert _cents(series.areas[0].total.kwh_per_eq) == Decimal("20.90")

    def test_fa_days_carry_their_drivers_and_flag_the_frozen_ones(self, built: Built) -> None:
        """The four F&A drivers on every day; those that add make the day's EQ."""
        series = build_cross(data_dir=DATA_DIR, engine=built.engine).cross(RANGE, areas=[FA_AREA])

        (area,) = series.areas
        assert {item.cuenta_stats for item in area.rows[0].drivers} == {
            "POS_ACT_MTL",
            "COMP_DEC",
            "FORM_PRD_SLI",
            "FORM_PRD_TRUCK",
        }
        for row in (*area.rows, area.total):
            assert sum(item.equs for item in row.drivers if item.suma_eq) == row.eq
        assert tuple(row.start for row in area.rows if row.frozen) == FROZEN_DAYS
        assert {row.energy_state for row in area.rows if not row.frozen} == {"ok"}
        assert area.total.driver("FORM_PRD_SLI").cantidad > 0

    def test_a_frozen_day_keeps_its_eq_but_has_no_ratio(self, built: Built) -> None:
        series = build_cross(data_dir=DATA_DIR, engine=built.engine).cross(
            DateRange(FROZEN_DAYS[0], FROZEN_DAYS[-1])
        )

        (area,) = series.areas
        assert all(row.kwh is None and row.kwh_per_eq is None for row in area.rows)
        assert sum(row.eq for row in area.rows) > 0
