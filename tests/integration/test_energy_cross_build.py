"""The energy-EQ cross on the real exports: the acceptance tests of the cross tool.

Layer: tests (integration)

Two things are checked, each skipped when its files are absent (they are not
in the repository):

* the SCADA export of F&A alone, with no database: exactly one frozen counter
  (rectifier 2, 2026-07-18 to 07-22, then a jump of 33,955.84 kWh), the days it
  leaves without kWh, and the energy of each month, to the cent;
* the cross against the EQ of a database built from the six workbooks of
  ``data/raw_excels``, as the Gold acceptance tests build it: kWh per EQ of
  F&A against FORMACION + DECORADO from 2026-07-02 to 2026-09-30 is **20.90**,
  the figure of ``notebooks/eda_cruce_equ_energia.ipynb``.

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
from energy.domain.equs_gold import DateRange
from energy.pipelines.flows.energy_cross import (
    FA_AREA,
    FA_WORKBOOK,
    SCADA_ZONE,
    build_cross,
    energy_sources,
)
from energy.pipelines.flows.equs_bronze_load import ROUTES, find_workbook, load_all
from energy.pipelines.flows.equs_gold import refresh_gold
from energy.services.bronze_loader import BronzeLoader

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from energy.adapters.scada_workbook import ScadaCounterSource

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
EXCELS_DIR = DATA_DIR / "raw_excels"

_NO_SCADA = not (DATA_DIR / FA_WORKBOOK).is_file()
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
FROZEN_DAYS = tuple(date(2026, 7, 18) + timedelta(days=offset) for offset in range(5))


def _cents(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(CENT, rounding=ROUND_HALF_UP)


def _source() -> ScadaCounterSource:
    (source,) = energy_sources(DATA_DIR)
    return source  # type: ignore[return-value]


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
        series = build_cross(data_dir=DATA_DIR, engine=built.engine).cross(RANGE, frequency="month")

        (area,) = series.areas
        assert [(row.start, _cents(row.eq), _cents(row.kwh_per_eq)) for row in area.rows] == [
            (date(2026, 7, 2), Decimal("51801.20"), Decimal("20.94")),
            (date(2026, 8, 1), Decimal("41993.63"), Decimal("20.72")),
            (date(2026, 9, 1), Decimal("51555.86"), Decimal("21.01")),
        ]

    def test_a_frozen_day_keeps_its_eq_but_has_no_ratio(self, built: Built) -> None:
        series = build_cross(data_dir=DATA_DIR, engine=built.engine).cross(
            DateRange(FROZEN_DAYS[0], FROZEN_DAYS[-1])
        )

        (area,) = series.areas
        assert all(row.kwh is None and row.kwh_per_eq is None for row in area.rows)
        assert sum(row.eq for row in area.rows) > 0
