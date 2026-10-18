"""Tests for the rules of the energy-EQ cross.

Layer: tests (unit)

The counter series are built by hand: three sibling meters read every hour at
half past, in Bogota time, so a local midnight always falls in the middle of an
interval and its interpolation is easy to state. Each test says what the
counters did and what the day must show.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from energy.domain.energy_cross import (
    CROSS_FIELDS,
    AreaCross,
    AreaEnergy,
    CounterReading,
    EnergyCrossSeries,
    classify_counters,
    counter_area_energy,
    cross_csv_rows,
    cross_json,
    cross_rows,
    daily_energy,
    describe_cross,
    energy_intensity,
    fiscal_label,
    fiscal_month,
    fiscal_year,
    meter_months,
    month_spans,
    monthly_area_energy,
)
from energy.domain.equs_gold import DateRange
from energy.domain.equs_series import PeriodTotal
from energy.domain.exceptions import DomainRuleError, NaiveDatetimeError

pytestmark = pytest.mark.unit

ZONE = ZoneInfo("America/Bogota")
METERS = ("R1", "R2", "R3")
# The first reading: 2026-07-01 at half past midnight, local time.
FIRST = datetime(2026, 7, 1, 0, 30, tzinfo=ZONE)
DAY1, DAY2, DAY3 = date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)


def _readings(*hourly: list[Decimal | int | None], start: datetime = FIRST) -> list[CounterReading]:
    """Turn the kWh of each hour of each meter into cumulative readings.

    ``None`` in an hour makes the reading that ends it blank; a counter that
    drops is written as a negative hour.
    """
    counters: list[Decimal | None] = [Decimal(1_000_000)] * len(hourly)
    readings = [CounterReading(start, tuple(counters))]
    for hour in range(len(hourly[0])):
        values: list[Decimal | None] = []
        for meter, series in enumerate(hourly):
            amount = series[hour]
            previous = counters[meter]
            if amount is None:
                values.append(None)
                continue
            base = previous if previous is not None else Decimal(1_000_000)
            counters[meter] = base + Decimal(amount)
            values.append(counters[meter])
        readings.append(CounterReading(start + timedelta(hours=hour + 1), tuple(values)))
    return readings


def _steady(hours: int, kwh: int = 100) -> list[Decimal | int | None]:
    return [kwh] * hours


# =============================================================================
# Fiscal calendar and the ratio
# =============================================================================
@pytest.mark.parametrize(
    ("day", "year", "month"),
    [
        (date(2025, 10, 1), 2026, 1),
        (date(2026, 1, 15), 2026, 4),
        (date(2026, 9, 30), 2026, 12),
        (date(2026, 10, 1), 2027, 1),
    ],
)
def test_the_fiscal_year_runs_october_to_september(day: date, year: int, month: int) -> None:
    """Named by the year it ends in."""
    assert fiscal_year(day) == year
    assert fiscal_month(day) == month


def test_a_fiscal_year_is_written_with_two_digits() -> None:
    assert fiscal_label(2026) == "FY26"
    assert fiscal_label(2100) == "FY00"


def test_kwh_per_eq_is_the_ratio_of_the_sums() -> None:
    assert energy_intensity(Decimal(400), Decimal(20)) == Decimal(20)


@pytest.mark.parametrize(
    ("kwh", "eq"),
    [
        (None, Decimal(10)),
        (Decimal(10), None),
        (Decimal(10), Decimal(0)),
        (Decimal(10), Decimal(-1)),
    ],
)
def test_kwh_per_eq_is_none_without_energy_or_production(
    kwh: Decimal | None, eq: Decimal | None
) -> None:
    """An idle period has no efficiency, not an infinite one."""
    assert energy_intensity(kwh, eq) is None


# =============================================================================
# Counters into days
# =============================================================================
def test_a_day_runs_from_local_midnight_to_local_midnight() -> None:
    """Steady 100 kWh an hour: every whole day is 2,400 kWh, interpolated at midnight."""
    counters = classify_counters(METERS, _readings(*[_steady(72)] * 3), ZONE)

    days = daily_energy(counters, DateRange(DAY2, DAY3))

    assert [(row.meter, row.day, row.kwh, row.state) for row in days[:3]] == [
        ("R1", DAY2, Decimal(2400), "ok"),
        ("R2", DAY2, Decimal(2400), "ok"),
        ("R3", DAY2, Decimal(2400), "ok"),
    ]
    assert all(row.coverage == 1 for row in days)


def test_midnight_splits_the_interval_that_contains_it() -> None:
    """The 23:30-00:30 hour holds 60 kWh: 30 go to each day."""
    hours: list[Decimal | int | None] = [0] * 72
    hours[23] = 60  # 2026-07-01 23:30 to 2026-07-02 00:30, local
    counters = classify_counters(("R1",), _readings(hours), ZONE)

    by_day = {row.day: row.kwh for row in daily_energy(counters, DateRange(DAY2, DAY2))}
    before = counters.kwh_between("R1", FIRST.astimezone(UTC), counters.midnight(DAY2))

    assert by_day == {DAY2: Decimal(30)}
    assert before == Decimal(30)


def test_a_day_the_readings_do_not_span_has_no_kwh() -> None:
    """The first reading is at 00:30, so the first day is not whole."""
    counters = classify_counters(METERS, _readings(*[_steady(48)] * 3), ZONE)

    first = daily_energy(counters, DateRange(DAY1, DAY1))

    assert {(row.kwh, row.state) for row in first} == {(None, "outside")}
    assert all(0 < row.coverage < 1 for row in first)


def test_a_real_stop_is_a_real_zero() -> None:
    """R1 and R2 stop for 10 hours while R3 runs, then restart gently: no frozen counter."""
    stopped = _steady(72)
    for hour in range(30, 40):
        stopped[hour] = 0
    stopped[40] = 10
    counters = classify_counters(METERS, _readings(stopped, list(stopped), _steady(72)), ZONE)

    days = {(row.meter, row.day): row for row in daily_energy(counters, DateRange(DAY2, DAY2))}

    assert counters.frozen == ()
    assert days["R1", DAY2].state == "ok"
    # Ten still hours lose 1,000 kWh; the restart hour has 10 instead of 100.
    assert days["R1", DAY2].kwh == Decimal(2400 - 1000 - 90)
    assert days["R3", DAY2].kwh == Decimal(2400)


def _frozen_series() -> list[CounterReading]:
    """R2 stands still for five hours of day 2 while R1 runs, then jumps 600 kWh in one."""
    frozen = _steady(72)
    for hour in range(30, 35):
        frozen[hour] = 0
    frozen[35] = 600
    return _readings(_steady(72), frozen, _steady(72, kwh=80))


def test_a_frozen_counter_leaves_its_days_without_kwh() -> None:
    """Five still hours and a jump above twice the usual maximum: missing, never 0."""
    counters = classify_counters(METERS, _frozen_series(), ZONE)

    days = {(row.meter, row.day): row for row in daily_energy(counters, DateRange(DAY2, DAY3))}

    (run,) = counters.frozen
    assert (run.meter, run.hours, run.jump_kwh) == ("R2", Decimal(5), Decimal(600))
    # 2 x the 99th percentile of R2's operating hours, the jump among them.
    assert run.limit_kwh_per_hour == 2 * (100 + 500 * Decimal("0.34"))
    assert run.start == (FIRST + timedelta(hours=30)).astimezone(UTC)
    assert (days["R2", DAY2].kwh, days["R2", DAY2].state) == (None, "frozen")
    assert days["R2", DAY2].coverage == Decimal(18) / 24
    assert days["R1", DAY2].kwh == Decimal(2400)
    assert days["R2", DAY3].kwh == Decimal(2400)


def test_a_frozen_counter_keeps_its_energy_over_a_longer_span() -> None:
    """The counter is cumulative: the two days together still hold every kWh."""
    counters = classify_counters(METERS, _frozen_series(), ZONE)

    both = counters.kwh_between("R2", counters.midnight(DAY2), counters.midnight(date(2026, 7, 4)))

    assert both == Decimal(4800)


@pytest.mark.parametrize(
    ("still", "jump", "sibling", "why"),
    [
        (2, 600, 100, "two hours is too short to tell"),
        (5, 150, 100, "a restart below the physical limit is a real stop"),
        (5, 600, 0, "every meter stopped, so nothing says the counter was stuck"),
    ],
)
def test_a_counter_is_frozen_only_with_all_three_signs(
    still: int, jump: int, sibling: int, why: str
) -> None:
    """At least three still hours, a moving sibling, and a jump above the limit."""
    r2 = _steady(72)
    r1 = _steady(72)
    for hour in range(30, 30 + still):
        r2[hour] = 0
        r1[hour] = sibling
    r2[30 + still] = jump
    counters = classify_counters(("R1", "R2"), _readings(r1, r2), ZONE)

    assert counters.frozen == (), why
    assert all(row.kwh is not None for row in daily_energy(counters, DateRange(DAY2, DAY3)))


def test_a_counter_that_loses_more_than_half_its_value_was_reset() -> None:
    """From 1,003,000 kWh down to 10: the meter started again, nothing spans it."""
    reset = _steady(72)
    reset[30] = -1_002_990
    counters = classify_counters(("R1",), _readings(reset), ZONE)

    (day2,) = daily_energy(counters, DateRange(DAY2, DAY2))

    assert (day2.kwh, day2.state) == (None, "reset")
    assert counters.kwh_between("R1", counters.midnight(DAY2), counters.midnight(DAY3)) is None


def test_a_small_drop_is_a_dip_that_leaves_its_day_but_not_its_month() -> None:
    """500 kWh down from 1,003,000 is a bad reading: its day is missing, never 0,
    and the counter difference across it still holds."""
    dip = _steady(72)
    dip[30] = -500
    counters = classify_counters(("R1",), _readings(dip), ZONE)

    day2, day3 = daily_energy(counters, DateRange(DAY2, DAY3))
    both = counters.kwh_between("R1", counters.midnight(DAY2), counters.midnight(date(2026, 7, 4)))

    assert counters.intervals["R1"][30].state == "dip"
    assert (day2.kwh, day2.state) == (None, "dip")
    assert (day3.kwh, day3.state) == (Decimal(2400), "ok")
    assert both == Decimal(4800 - 100 - 500)


def test_a_blank_reading_leaves_its_day_without_kwh() -> None:
    blank = _steady(72)
    blank[30] = None
    counters = classify_counters(("R1",), _readings(blank), ZONE)

    (day2,) = daily_energy(counters, DateRange(DAY2, DAY2))

    assert (day2.kwh, day2.state) == (None, "unread")


def test_readings_must_carry_a_time_zone() -> None:
    naive = CounterReading(datetime(2026, 7, 1, 0, 30), (Decimal(1),))  # noqa: DTZ001 - the point of the test
    later = CounterReading(FIRST + timedelta(hours=1), (Decimal(2),))

    with pytest.raises(NaiveDatetimeError):
        classify_counters(("R1",), [naive, later], ZONE)


@pytest.mark.parametrize(
    "readings",
    [
        [CounterReading(FIRST, (Decimal(1),))],
        [CounterReading(FIRST, (Decimal(1),)), CounterReading(FIRST, (Decimal(2),))],
        [CounterReading(FIRST, (Decimal(1),)), CounterReading(FIRST + timedelta(hours=1), ())],
    ],
    ids=["one reading", "two at one instant", "a counter short"],
)
def test_a_counter_series_that_cannot_be_read_is_refused(readings: list[CounterReading]) -> None:
    with pytest.raises(DomainRuleError):
        classify_counters(("R1",), readings, ZONE)


def test_readings_may_arrive_in_any_order() -> None:
    """The gas export, for one, lists the newest reading first."""
    readings = _readings(_steady(48))
    counters = classify_counters(("R1",), reversed(readings), ZONE)

    assert counters.first_read == FIRST.astimezone(UTC)
    assert daily_energy(counters, DateRange(DAY2, DAY2))[0].kwh == Decimal(2400)


# =============================================================================
# The energy of an area
# =============================================================================
def test_an_area_has_a_day_only_when_every_meter_has_it() -> None:
    counters = classify_counters(METERS, _frozen_series(), ZONE)

    energy = counter_area_energy("F&A", "export.xlsx", counters, DateRange(DAY2, DAY3))

    assert [(span.start, span.kwh, span.measured_days) for span in energy.days] == [
        (DAY2, None, 0),
        (DAY3, Decimal(2400 + 2400 + 1920), 1),
    ]
    assert energy.days[0].coverage == (1 + Decimal(18) / 24 + 1) / 3
    assert energy.total.kwh == Decimal(2 * (2400 + 2400 + 1920))
    assert energy.total.measured_days == 1
    assert energy.frozen == counters.frozen
    assert energy.zone is ZONE


def test_months_are_clipped_to_the_range() -> None:
    period = DateRange(date(2026, 8, 15), date(2026, 10, 2))

    assert month_spans(period) == (
        DateRange(date(2026, 8, 15), date(2026, 8, 31)),
        DateRange(date(2026, 9, 1), date(2026, 9, 30)),
        DateRange(date(2026, 10, 1), date(2026, 10, 2)),
    )


def test_a_month_is_the_counter_difference_between_its_midnights() -> None:
    """Across a month boundary, each month gets its own share of the counters."""
    start = datetime(2026, 7, 30, 0, 30, tzinfo=ZONE)
    counters = classify_counters(("R1",), _readings(_steady(96), start=start), ZONE)

    energy = counter_area_energy(
        "F&A", "x", counters, DateRange(date(2026, 7, 31), date(2026, 8, 2))
    )

    assert [(span.start, span.end, span.kwh) for span in energy.months] == [
        (date(2026, 7, 31), date(2026, 7, 31), Decimal(2400)),
        (date(2026, 8, 1), date(2026, 8, 2), Decimal(4800)),
    ]
    assert [span.measured_days for span in energy.months] == [1, 2]


# =============================================================================
# The cross
# =============================================================================
def _area_cross() -> tuple[AreaCross, EnergyCrossSeries]:
    """Two days, read from midnight: 120 kWh against 12 EQ, then 360 kWh against 72 EQ."""
    hours: list[Decimal | int | None] = [5] * 24 + [15] * 24
    start = datetime(2026, 7, 2, tzinfo=ZONE)
    counters = classify_counters(("R1",), _readings(hours, start=start), ZONE)
    period = DateRange(DAY2, DAY3)
    energy = counter_area_energy("F&A", "export.xlsx", counters, period)
    eq_days = {
        DAY2: PeriodTotal(DAY2, {"FORMACION": Decimal(8), "DECORADO": Decimal(4)}, Decimal(12)),
        DAY3: PeriodTotal(DAY3, {"FORMACION": Decimal(48), "DECORADO": Decimal(24)}, Decimal(72)),
    }
    rows, total, without = cross_rows([energy], eq_days, ("FORMACION", "DECORADO"), "day")
    area = AreaCross(
        (energy,), ("FORMACION", "DECORADO"), rows, total, without, (date(2026, 7, 1),)
    )
    return area, EnergyCrossSeries(period, "day", (area,), monthly_only=("OREC",))


def test_the_cross_sets_each_day_against_the_eq_of_the_same_day() -> None:
    area, _ = _area_cross()

    assert [(row.start, row.kwh, row.eq, row.eq_by_process["DECORADO"]) for row in area.rows] == [
        (DAY2, Decimal(120), Decimal(12), Decimal(4)),
        (DAY3, Decimal(360), Decimal(72), Decimal(24)),
    ]
    assert [row.kwh_per_eq for row in area.rows] == [Decimal(10), Decimal(5)]


def test_the_total_is_the_ratio_of_the_sums_not_the_mean_of_the_ratios() -> None:
    """(120 + 360) / (12 + 72) = 5.71, where the mean of 10 and 5 would be 7.5."""
    area, _ = _area_cross()

    assert area.total.kwh_per_eq == Decimal(480) / 84


def test_the_cross_renders_as_a_table_json_and_csv() -> None:
    _, series = _area_cross()

    table = "\n".join(describe_cross(series))
    payload = cross_json(series)
    csv_rows = cross_csv_rows(series)

    assert "F&A against FORMACION + DECORADO; energy, in priority order: export.xlsx" in table
    assert "2026-07-02" in table
    assert "EQ: the days add up to gold.v_resumen_equ exactly: 2026-07." in table
    assert "Monthly energy only, so left out of the daily cross: OREC." in table
    assert payload["areas"][0]["total"]["kwh_por_eq"] == 5.7143
    assert payload["areas"][0]["total"]["fuente_energia"] == "export.xlsx"
    assert payload["areas"][0]["filas"][1]["eq_procesos"] == {"FORMACION": 48, "DECORADO": 24}
    assert payload["solo_mensual"] == ["OREC"]
    assert csv_rows[0] == list(CROSS_FIELDS)
    assert csv_rows[1][:5] == ["F&A", "2026-07-02", "2026-07-02", "2026-07-02", 1]
    assert csv_rows[1][-1] == "export.xlsx"


# =============================================================================
# Monthly sources and priority
# =============================================================================
JULY, AUGUST, SEPTEMBER = date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)
SUMMER = DateRange(JULY, date(2026, 9, 30))


def _book(months: dict[date, Decimal | None], period: DateRange = SUMMER) -> AreaEnergy:
    return monthly_area_energy("F&A", "book.xlsx", months, period)


def test_a_monthly_source_answers_whole_months_only() -> None:
    """A month cut by the range cannot be split, so it has no kWh."""
    period = DateRange(date(2026, 7, 15), date(2026, 9, 30))

    energy = _book({JULY: Decimal(100), AUGUST: Decimal(200), SEPTEMBER: Decimal(300)}, period)

    assert energy.resolution == "month"
    assert energy.days == ()
    assert [(span.start, span.kwh, span.coverage) for span in energy.months] == [
        (date(2026, 7, 15), None, 0),
        (AUGUST, Decimal(200), 1),
        (SEPTEMBER, Decimal(300), 1),
    ]
    assert energy.total.kwh is None
    assert energy.total.coverage == Decimal(61) / 78


def test_a_monthly_source_has_a_total_when_every_month_is_whole_and_known() -> None:
    energy = _book({JULY: Decimal(100), AUGUST: Decimal(200), SEPTEMBER: Decimal(300)})
    missing = _book({JULY: Decimal(100), AUGUST: Decimal(200)})

    assert (energy.total.kwh, energy.total.coverage) == (Decimal(600), 1)
    assert missing.total.kwh is None


def _eq_days(per_day: int = 10) -> dict[date, PeriodTotal]:
    days = {}
    day = JULY
    while day <= SUMMER.end:
        days[day] = PeriodTotal(day, {"FORMACION": Decimal(per_day)}, Decimal(per_day))
        day += timedelta(days=1)
    return days


def _counters_from(first: date) -> AreaEnergy:
    """1 kWh an hour from 00:30 local on a day until after September."""
    start = datetime(first.year, first.month, first.day, 0, 30, tzinfo=ZONE)
    hours = int((datetime(2026, 10, 1, 1, tzinfo=ZONE) - start) / timedelta(hours=1))
    counters = classify_counters(("R1",), _readings([1] * hours, start=start), ZONE)
    return counter_area_energy("F&A", "scada.xlsx", counters, SUMMER)


def test_each_month_takes_the_first_source_that_covers_it_whole() -> None:
    """SCADA starts on 1 July at 00:30, so July is the book's and August SCADA's."""
    scada = _counters_from(JULY)
    book = _book({JULY: Decimal(1000), AUGUST: Decimal(2000)})

    rows, total, without = cross_rows([scada, book], _eq_days(), ("FORMACION",), "month")

    assert [(row.start, row.kwh, row.energy_source) for row in rows] == [
        (JULY, Decimal(1000), "book.xlsx"),
        (AUGUST, Decimal(744), "scada.xlsx"),
        (SEPTEMBER, Decimal(720), "scada.xlsx"),
    ]
    assert total.energy_source == "book.xlsx + scada.xlsx"
    assert (total.kwh, total.eq, without) == (Decimal(1000 + 744 + 720), Decimal(920), 0)


def test_a_total_pairs_the_kwh_with_the_eq_of_the_same_months_only() -> None:
    """September has EQ and no energy: its EQ stays out of the ratio and is reported."""
    book = _book({JULY: Decimal(1000), AUGUST: Decimal(2000)})

    rows, total, without = cross_rows([book], _eq_days(), ("FORMACION",), "month")

    assert (rows[2].kwh, rows[2].energy_source, rows[2].eq) == (None, None, Decimal(300))
    assert (total.kwh, total.eq) == (Decimal(3000), Decimal(620))
    assert total.kwh_per_eq == Decimal(3000) / 620
    assert without == Decimal(300)
    assert total.coverage == Decimal(62) / 92


def test_a_source_that_covers_the_whole_range_gives_the_total() -> None:
    """The counters' own total, the difference between the range's midnights, comes first."""
    scada = _counters_from(date(2026, 6, 30))
    book = _book({JULY: Decimal(1000), AUGUST: Decimal(2000), SEPTEMBER: Decimal(3000)})

    _, total, without = cross_rows([scada, book], _eq_days(), ("FORMACION",), "month")

    assert (total.kwh, total.energy_source, without) == (Decimal(92 * 24), "scada.xlsx", 0)


def test_a_daily_cross_takes_only_sources_with_days() -> None:
    with pytest.raises(DomainRuleError):
        cross_rows([_book({JULY: Decimal(1)})], _eq_days(), ("FORMACION",), "day")
    with pytest.raises(DomainRuleError):
        cross_rows([], _eq_days(), ("FORMACION",), "month")


def test_each_meter_can_be_read_month_by_month() -> None:
    start = datetime(2026, 7, 30, 0, 30, tzinfo=ZONE)
    readings = _readings(_steady(96), _steady(96, 10), start=start)
    counters = classify_counters(("R1", "R2"), readings, ZONE)

    months = meter_months(counters, DateRange(date(2026, 7, 31), date(2026, 8, 2)))

    assert [span.kwh for span in months["R1"]] == [Decimal(2400), Decimal(4800)]
    assert [span.kwh for span in months["R2"]] == [Decimal(240), Decimal(480)]


def test_a_total_never_mixes_one_source_s_months_with_another_s_total() -> None:
    """The book has all three months, but SCADA gave August and September: add the rows."""
    scada = _counters_from(JULY)
    book = _book({JULY: Decimal(1000), AUGUST: Decimal(2000), SEPTEMBER: Decimal(3000)})

    rows, total, _ = cross_rows([scada, book], _eq_days(), ("FORMACION",), "month")

    assert total.kwh == sum((row.kwh for row in rows if row.kwh is not None), start=Decimal(0))
    assert total.energy_source == "book.xlsx + scada.xlsx"
