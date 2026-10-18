"""The energy of a plant area set against its equivalent batteries.

Layer: domain

Responsibility:
    Holds the rules of the energy-EQ cross: the fiscal calendar, kWh per EQ as a
    ratio of sums, the turning of cumulative energy counters into the kWh of
    each local day, and what one row of the cross is.

    **Counters.** A SCADA export gives each meter's cumulative counter at
    irregular instants (about one an hour, never on the hour). The kWh of an
    interval is the difference between two consecutive readings. Two things
    make an interval unusable, and an unusable interval is *missing*, never 0:

    * a counter that goes down (a reset) or a reading that is blank;
    * a *frozen counter*: the counter stops for at least
      :data:`FROZEN_MIN_HOURS` while a sibling meter keeps moving, and the
      interval right after the run jumps above the meter's physical limit
      (:data:`PHYSICAL_LIMIT_FACTOR` times the 99th percentile of its operating
      hours). The run and the jump are both missing: the energy is real, but
      when it was used is not known. A run that does not end in a jump is a
      real stop, and its zeros are real zeros.

    **Days.** A day is a local calendar day, as ``fecha_efectiva`` is. Readings
    never fall on midnight, so the counter is interpolated linearly at each
    local midnight inside the interval that contains it: the kWh of day D is
    the counter at D+1 00:00 minus the counter at D 00:00. A day touched by a
    missing interval has no kWh.

    **Periods.** The kWh of a month or a range is the counter difference
    between its first and its last midnight. A counter is cumulative, so that
    difference is exact even when the counter froze inside the period: freezing
    hides *when* the energy was used, not *how much*. It needs both boundaries
    to fall inside usable intervals and no reset between them.

    **Ratios.** kWh per EQ is always Σ kWh / Σ EQ over the same days, never the
    mean of daily or monthly ratios. It is not
    :func:`energy.domain.kpi_formulas.energy_intensity`, which is energy per
    square metre of floor.

Depends on:
    ``energy.domain.equs_gold``, ``energy.domain.equs_series``,
    ``energy.domain.exceptions``, standard library only.
"""

from __future__ import annotations

import bisect
import itertools
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, Final, Literal

from energy.domain.equs_gold import DateRange, json_number, month_end, month_label
from energy.domain.equs_series import Frequency, PeriodTotal, period_label
from energy.domain.exceptions import DomainRuleError, NaiveDatetimeError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import tzinfo

# The fiscal year runs October to September and is named by the year it ends in:
# FY26 is October 2025 to September 2026.
FY_START_MONTH: Final = 10

# A counter standing still this long while a sibling moves may be frozen.
FROZEN_MIN_HOURS: Final = Decimal(3)
# ... and is, when the next interval exceeds this many times its usual maximum.
PHYSICAL_LIMIT_FACTOR: Final = Decimal(2)
# The percentile of the operating hours that is the "usual maximum".
LIMIT_QUANTILE: Final = Decimal("0.99")
# Below this many kWh in an hour, a meter counts as off.
OPERATING_KWH_PER_HOUR: Final = Decimal(50)

KWH_PLACES: Final = 2
RATIO_PLACES: Final = 4
COVERAGE_PLACES: Final = 4

_ZERO = Decimal(0)
_ONE = Decimal(1)
_CENT = Decimal("0.01")
_MICROS_PER_HOUR = Decimal(3_600_000_000)
_MICROSECOND = timedelta(microseconds=1)

IntervalState = Literal["ok", "frozen", "jump", "reset", "unread"]
DayState = Literal["ok", "frozen", "reset", "unread", "outside"]
Resolution = Literal["day", "month"]

# The columns of one row of the cross, in CSV order.
CROSS_FIELDS: Final = (
    "area",
    "periodo",
    "desde",
    "hasta",
    "dias",
    "dias_medidos",
    "cobertura",
    "kwh",
    "eq",
    "kwh_por_eq",
)


# =============================================================================
# Fiscal calendar and the ratio
# =============================================================================
def fiscal_year(day: date) -> int:
    """Return the fiscal year a day belongs to: October 2025 to September 2026 is 2026."""
    return day.year + (1 if day.month >= FY_START_MONTH else 0)


def fiscal_month(day: date) -> int:
    """Return the position of a day's month in its fiscal year: 1 is October, 12 September."""
    return (day.month - FY_START_MONTH) % 12 + 1


def fiscal_label(year: int) -> str:
    """Return a fiscal year as the business writes it: 2026 is ``FY26``."""
    return f"FY{year % 100:02d}"


def energy_intensity(kwh: Decimal | None, eq: Decimal | None) -> Decimal | None:
    """Return kWh per equivalent battery, from the sums of one period.

    Pass the total kWh and the total EQ of the same days; never average the
    ratios of shorter periods.

    Args:
        kwh: the energy of the period; ``None`` when it is not known.
        eq: the equivalent batteries of the same period.

    Returns:
        ``kwh / eq``, or ``None`` when either is missing or no EQ was produced
        (an idle period has no efficiency, not an infinite one).
    """
    if kwh is None or eq is None or eq <= 0:
        return None
    return kwh / eq


# =============================================================================
# Counters
# =============================================================================
@dataclass(frozen=True, slots=True)
class CounterReading:
    """The cumulative counters of every meter at one instant.

    Attributes:
        read_at: when the counters were read; timezone-aware.
        kwh: the cumulative kWh of each meter, in the order of the meters;
            ``None`` where the cell was blank or not a number.
    """

    read_at: datetime
    kwh: tuple[Decimal | None, ...]


@dataclass(frozen=True, slots=True)
class Interval:
    """One meter between two consecutive readings.

    Attributes:
        start: the first reading's instant, in UTC.
        end: the second reading's instant, in UTC.
        counter_start: the counter at ``start``, in kWh; ``None`` if blank.
        counter_end: the counter at ``end``, in kWh; ``None`` if blank.
        state: ``ok``, ``frozen`` (part of a frozen run), ``jump`` (the interval
            that releases a frozen run), ``reset`` (the counter went down) or
            ``unread`` (a reading is blank).
    """

    start: datetime
    end: datetime
    counter_start: Decimal | None
    counter_end: Decimal | None
    state: IntervalState

    @property
    def difference(self) -> Decimal | None:
        """What the counter gained, whatever the state; ``None`` if a reading is blank."""
        if self.counter_start is None or self.counter_end is None:
            return None
        return self.counter_end - self.counter_start

    @property
    def kwh(self) -> Decimal | None:
        """The energy of the interval; ``None`` unless the state is ``ok``."""
        return self.difference if self.state == "ok" else None

    @property
    def hours(self) -> Decimal:
        """How long the interval lasts, in hours."""
        return _micros(self.end - self.start) / _MICROS_PER_HOUR

    def counter_at(self, instant: datetime) -> Decimal | None:
        """Interpolate the counter linearly at an instant inside the interval."""
        if self.counter_start is None or self.counter_end is None:
            return None
        share = _micros(instant - self.start) / _micros(self.end - self.start)
        return self.counter_start + (self.counter_end - self.counter_start) * share


@dataclass(frozen=True, slots=True)
class FrozenRun:
    """A counter that stood still and then jumped.

    Attributes:
        meter: the meter.
        start: the reading the counter stopped at, in UTC.
        end: the reading the jump starts from, in UTC.
        jump_end: the reading the jump reaches, in UTC.
        hours: how long the counter stood still.
        jump_kwh: what the counter gained in the jump.
        limit_kwh_per_hour: the physical limit the jump exceeded.
    """

    meter: str
    start: datetime
    end: datetime
    jump_end: datetime
    hours: Decimal
    jump_kwh: Decimal
    limit_kwh_per_hour: Decimal


@dataclass(frozen=True, slots=True)
class MeterDay:
    """The kWh of one meter on one local day.

    Attributes:
        meter: the meter.
        day: the local day.
        kwh: the counter at the next midnight minus the counter at this one;
            ``None`` when the day is not wholly measured.
        coverage: the share of the day inside usable intervals, 0 to 1.
        state: ``ok``; the worst interval touching the day (``reset``,
            ``unread``, ``frozen``); or ``outside`` when the readings do not
            span the whole day.
    """

    meter: str
    day: date
    kwh: Decimal | None
    coverage: Decimal
    state: DayState


@dataclass(frozen=True, slots=True)
class CounterEnergy:
    """The intervals of a set of sibling meters, classified.

    Attributes:
        meters: the meters, in the order of the readings.
        zone: the time zone whose midnights end a day.
        read_at: every reading's instant, in UTC, in order.
        intervals: the intervals of each meter, in time order; every meter has
            the same instants.
        limits: the physical limit of each meter in kWh per hour; ``None`` when
            the meter never operated.
        frozen: the frozen runs found, in time order.
    """

    meters: tuple[str, ...]
    zone: tzinfo
    read_at: tuple[datetime, ...]
    intervals: Mapping[str, tuple[Interval, ...]]
    limits: Mapping[str, Decimal | None]
    frozen: tuple[FrozenRun, ...]

    @property
    def first_read(self) -> datetime:
        """The first reading, in UTC."""
        return self.read_at[0]

    @property
    def last_read(self) -> datetime:
        """The last reading, in UTC."""
        return self.read_at[-1]

    def midnight(self, day: date) -> datetime:
        """Return the local midnight that starts a day, in UTC."""
        return datetime.combine(day, time(0), tzinfo=self.zone).astimezone(UTC)

    def overlapping(self, meter: str, start: datetime, end: datetime) -> tuple[Interval, ...]:
        """Return the intervals of a meter that overlap ``[start, end)``."""
        intervals = self.intervals[meter]
        first = max(bisect.bisect_right(self.read_at, start) - 1, 0)
        last = bisect.bisect_left(self.read_at, end)
        return tuple(
            item for item in intervals[first:last] if item.start < end and item.end > start
        )

    def counter_at(self, meter: str, instant: datetime) -> Decimal | None:
        """Return the counter of a meter at an instant, interpolated linearly.

        The instant must fall inside a usable interval, ends included: inside a
        frozen run the counter is stale, and inside a reset or a blank reading it
        is not known.
        """
        intervals = self.intervals[meter]
        position = bisect.bisect_right(self.read_at, instant) - 1
        # An instant on a reading belongs to the interval ending there and to the
        # one starting there; either may be the usable one.
        for index in (position, position - 1):
            if 0 <= index < len(intervals):
                item = intervals[index]
                if item.state == "ok" and item.start <= instant <= item.end:
                    return item.counter_at(instant)
        return None

    def kwh_between(self, meter: str, start: datetime, end: datetime) -> Decimal | None:
        """Return the energy of a meter between two instants.

        The counter difference is exact across a frozen run, so only a reset or
        a blank reading between the two instants, or an instant at which the
        counter is not known, leaves it unknown.
        """
        if any(item.state in ("reset", "unread") for item in self.overlapping(meter, start, end)):
            return None
        at_start = self.counter_at(meter, start)
        at_end = self.counter_at(meter, end)
        if at_start is None or at_end is None:
            return None
        return at_end - at_start

    def coverage(self, meter: str, start: datetime, end: datetime) -> Decimal:
        """Return the share of ``[start, end)`` that lies inside usable intervals."""
        covered = sum(
            (
                _micros(min(item.end, end) - max(item.start, start))
                for item in self.overlapping(meter, start, end)
                if item.state == "ok"
            ),
            start=_ZERO,
        )
        total = _micros(end - start)
        return covered / total if total else _ZERO


def _micros(span: timedelta) -> Decimal:
    return Decimal(span // _MICROSECOND)


def _utc(instant: datetime) -> datetime:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise NaiveDatetimeError("a counter reading has no time zone", read_at=instant.isoformat())
    return instant.astimezone(UTC)


def _quantile(values: Sequence[Decimal], q: Decimal) -> Decimal:
    """Linear-interpolation quantile, as numpy and pandas compute it by default."""
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def classify_counters(
    meters: Sequence[str],
    readings: Iterable[CounterReading],
    zone: tzinfo,
    *,
    frozen_hours: Decimal = FROZEN_MIN_HOURS,
    limit_factor: Decimal = PHYSICAL_LIMIT_FACTOR,
    operating_kwh: Decimal = OPERATING_KWH_PER_HOUR,
) -> CounterEnergy:
    """Turn the readings of sibling meters into classified intervals.

    Args:
        meters: the meters, in the order of each reading's counters.
        readings: the readings, in any order; timezone-aware.
        zone: the time zone whose midnights end a day.
        frozen_hours: how long a counter must stand still to be suspected.
        limit_factor: how many times the usual maximum a jump must exceed.
        operating_kwh: below this many kWh per hour a meter counts as off.

    Returns:
        The intervals, the physical limits and the frozen runs.

    Raises:
        NaiveDatetimeError: if a reading has no time zone.
        DomainRuleError: if there are fewer than two readings, two readings
            share an instant, or a reading does not have one counter per meter.
    """
    names = tuple(meters)
    ordered = sorted(
        (CounterReading(_utc(reading.read_at), reading.kwh) for reading in readings),
        key=lambda reading: reading.read_at,
    )
    if len(ordered) < 2:  # noqa: PLR2004 - an interval needs two readings
        raise DomainRuleError("a counter series needs at least two readings", readings=len(ordered))
    for reading in ordered:
        if len(reading.kwh) != len(names):
            raise DomainRuleError(
                "a reading must have one counter per meter",
                read_at=reading.read_at.isoformat(),
                meters=len(names),
                counters=len(reading.kwh),
            )
    pairs = list(itertools.pairwise(ordered))
    for before, after in pairs:
        if before.read_at == after.read_at:
            raise DomainRuleError(
                "two readings share an instant", read_at=before.read_at.isoformat()
            )

    intervals: dict[str, list[Interval]] = {
        meter: [
            _interval(before.read_at, after.read_at, before.kwh[column], after.kwh[column])
            for before, after in pairs
        ]
        for column, meter in enumerate(names)
    }
    limits = {meter: _limit(intervals[meter], limit_factor, operating_kwh) for meter in names}
    # Detect on the intervals as read, so one meter's frozen run cannot hide or
    # create another's.
    as_read = {meter: tuple(intervals[meter]) for meter in names}
    frozen: list[FrozenRun] = []
    for meter in names:
        siblings = [as_read[other] for other in names if other != meter]
        frozen += _freeze(meter, intervals[meter], siblings, limits[meter], frozen_hours)

    return CounterEnergy(
        meters=names,
        zone=zone,
        read_at=tuple(reading.read_at for reading in ordered),
        intervals={meter: tuple(intervals[meter]) for meter in names},
        limits=limits,
        frozen=tuple(sorted(frozen, key=lambda run: (run.start, run.meter))),
    )


def _interval(
    start: datetime, end: datetime, before: Decimal | None, after: Decimal | None
) -> Interval:
    if before is None or after is None:
        return Interval(start, end, before, after, "unread")
    return Interval(start, end, before, after, "reset" if after < before else "ok")


def _rate(interval: Interval) -> Decimal | None:
    kwh = interval.kwh
    return kwh / interval.hours if kwh is not None else None


def _limit(
    intervals: Sequence[Interval], factor: Decimal, operating_kwh: Decimal
) -> Decimal | None:
    rates = [
        rate for item in intervals if (rate := _rate(item)) is not None and rate > operating_kwh
    ]
    return factor * _quantile(rates, LIMIT_QUANTILE) if rates else None


def _still(
    intervals: Sequence[Interval], siblings: Sequence[Sequence[Interval]], index: int
) -> bool:
    """The meter stands still at an interval while a sibling moves."""
    if intervals[index].kwh != 0:
        return False
    return any((other[index].kwh or _ZERO) > 0 for other in siblings)


def _freeze(
    meter: str,
    intervals: list[Interval],
    siblings: Sequence[Sequence[Interval]],
    limit: Decimal | None,
    frozen_hours: Decimal,
) -> list[FrozenRun]:
    """Find the frozen runs of one meter and mark their intervals, in place."""
    if limit is None:
        return []
    runs: list[FrozenRun] = []
    index = 0
    while index < len(intervals):
        if not _still(intervals, siblings, index):
            index += 1
            continue
        first = index
        while index < len(intervals) and _still(intervals, siblings, index):
            index += 1
        hours = sum((intervals[i].hours for i in range(first, index)), start=_ZERO)
        if hours < frozen_hours or index >= len(intervals):
            continue  # too short to tell, or the readings end before any jump
        jump = intervals[index]
        rate = _rate(jump)
        if rate is None or rate <= limit:
            continue  # a real stop: its zeros are real
        for position in range(first, index + 1):
            item = intervals[position]
            state: IntervalState = "jump" if position == index else "frozen"
            intervals[position] = Interval(
                item.start, item.end, item.counter_start, item.counter_end, state
            )
        runs.append(
            FrozenRun(
                meter=meter,
                start=intervals[first].start,
                end=jump.start,
                jump_end=jump.end,
                hours=hours,
                jump_kwh=jump.difference or _ZERO,
                limit_kwh_per_hour=limit,
            )
        )
        index += 1
    return runs


_DAY_STATE: Final[Mapping[IntervalState, DayState]] = {
    "jump": "frozen",
    "frozen": "frozen",
    "reset": "reset",
    "unread": "unread",
    "ok": "ok",
}
_DAY_SEVERITY: Final[tuple[DayState, ...]] = ("reset", "unread", "frozen")


def daily_energy(counters: CounterEnergy, period: DateRange) -> tuple[MeterDay, ...]:
    """Return the kWh of each meter on each local day of a range.

    A day's kWh is the counter interpolated at the next local midnight minus the
    counter interpolated at this one. A day the readings do not span, or that a
    missing interval touches, has no kWh: missing is never 0.

    Args:
        counters: the classified intervals.
        period: the days, both ends included.

    Returns:
        One row per day and meter, by day and then in the order of the meters.
    """
    rows: list[MeterDay] = []
    for offset in range(period.days):
        day = period.start + timedelta(days=offset)
        start, end = counters.midnight(day), counters.midnight(day + timedelta(days=1))
        for meter in counters.meters:
            coverage = counters.coverage(meter, start, end)
            if start < counters.first_read or end > counters.last_read:
                rows.append(MeterDay(meter, day, None, coverage, "outside"))
                continue
            states = {_DAY_STATE[item.state] for item in counters.overlapping(meter, start, end)}
            worst = next((state for state in _DAY_SEVERITY if state in states), None)
            if worst is not None:
                rows.append(MeterDay(meter, day, None, coverage, worst))
                continue
            kwh = counters.kwh_between(meter, start, end)
            rows.append(MeterDay(meter, day, kwh, coverage, "ok"))
    return tuple(rows)


# =============================================================================
# The energy of an area
# =============================================================================
@dataclass(frozen=True, slots=True)
class EnergySpan:
    """The energy of an area over a day, a month or a whole range.

    Attributes:
        start: the first day.
        end: the last day, included.
        kwh: the energy; ``None`` when it is not known.
        coverage: the share of the span that was measured, 0 to 1.
        measured_days: how many days of the span have a kWh of their own;
            ``None`` for a source that has no days.
    """

    start: date
    end: date
    kwh: Decimal | None
    coverage: Decimal
    measured_days: int | None = None

    @property
    def days(self) -> int:
        """How many days the span covers."""
        return (self.end - self.start).days + 1


@dataclass(frozen=True, slots=True)
class AreaEnergy:
    """The energy of one plant area over a range, from one source.

    A source with daily data fills ``days``; one with only monthly data (a
    monthly workbook) leaves it empty, and its months answer only the
    monthly cross.

    Attributes:
        area: the plant area, as the EQ data names it (``F&A``).
        source: where the energy comes from, for the reader.
        resolution: ``day`` or ``month``, the finest the source answers.
        period: the range asked for.
        meters: the meters added up, if the source has meters.
        days: one span per day of the range.
        months: one span per month the range touches, clipped to the range.
        total: the whole range.
        meter_days: the kWh of each meter on each day.
        frozen: the frozen counters found in the source.
        zone: the time zone of the area's days, to show instants in.
    """

    area: str
    source: str
    resolution: Resolution
    period: DateRange
    meters: tuple[str, ...]
    days: tuple[EnergySpan, ...]
    months: tuple[EnergySpan, ...]
    total: EnergySpan
    meter_days: tuple[MeterDay, ...] = ()
    frozen: tuple[FrozenRun, ...] = ()
    zone: tzinfo = UTC


def month_spans(period: DateRange) -> tuple[DateRange, ...]:
    """Return each month a range touches, clipped to the range."""
    return tuple(
        DateRange(max(month, period.start), min(month_end(month), period.end))
        for month in period.months
    )


def _span(counters: CounterEnergy, span: DateRange, measured_days: int) -> EnergySpan:
    start, end = counters.midnight(span.start), counters.midnight(span.end + timedelta(days=1))
    values = [counters.kwh_between(meter, start, end) for meter in counters.meters]
    coverage = sum(
        (counters.coverage(meter, start, end) for meter in counters.meters), start=_ZERO
    ) / len(counters.meters)
    return EnergySpan(span.start, span.end, _sum_all(values), coverage, measured_days)


def _sum_all(values: Sequence[Decimal | None]) -> Decimal | None:
    """Add values up; ``None`` when any is missing, since part of a sum is not the sum."""
    total = _ZERO
    for value in values:
        if value is None:
            return None
        total += value
    return total


def counter_area_energy(
    area: str, source: str, counters: CounterEnergy, period: DateRange
) -> AreaEnergy:
    """Add up the meters of an area by day, by month and over the range.

    A day has kWh only when every meter has kWh that day. A month and the whole
    range take each meter's counter difference between their first and last
    midnight, so a frozen counter inside them does not lose its energy.

    Args:
        area: the plant area.
        source: where the counters come from.
        counters: the classified intervals of the area's meters.
        period: the days, both ends included.

    Returns:
        The energy of the area.
    """
    meter_days = daily_energy(counters, period)
    by_day: dict[date, list[MeterDay]] = {}
    for row in meter_days:
        by_day.setdefault(row.day, []).append(row)

    days = []
    for day, rows in by_day.items():
        kwh = _sum_all([row.kwh for row in rows])
        coverage = sum((row.coverage for row in rows), start=_ZERO) / len(rows)
        days.append(EnergySpan(day, day, kwh, coverage, 0 if kwh is None else 1))
    measured = {span.start for span in days if span.kwh is not None}

    def count(span: DateRange) -> int:
        return sum(1 for day in measured if span.start <= day <= span.end)

    return AreaEnergy(
        area=area,
        source=source,
        resolution="day",
        period=period,
        meters=counters.meters,
        days=tuple(days),
        months=tuple(_span(counters, span, count(span)) for span in month_spans(period)),
        total=_span(counters, period, count(period)),
        meter_days=meter_days,
        frozen=tuple(
            run
            for run in counters.frozen
            if run.start < counters.midnight(period.end + timedelta(days=1))
            and run.jump_end > counters.midnight(period.start)
        ),
        zone=counters.zone,
    )


# =============================================================================
# The cross
# =============================================================================
@dataclass(frozen=True, slots=True)
class CrossRow:
    """The energy of an area against its EQ over one day, month or range.

    Attributes:
        area: the plant area.
        start: the first day.
        end: the last day, included.
        kwh: the energy; ``None`` when it is not known.
        coverage: the share of the span whose energy was measured, 0 to 1.
        measured_days: the days of the span with a kWh of their own; ``None``
            for a source without days.
        eq: the EQ of the area's battery processes over the same days.
        eq_by_process: the EQ of each of those processes.
    """

    area: str
    start: date
    end: date
    kwh: Decimal | None
    coverage: Decimal
    measured_days: int | None
    eq: Decimal
    eq_by_process: dict[str, Decimal]

    @property
    def days(self) -> int:
        """How many days the row covers."""
        return (self.end - self.start).days + 1

    @property
    def kwh_per_eq(self) -> Decimal | None:
        """Σ kWh / Σ EQ of the row; ``None`` without kWh or without EQ."""
        return energy_intensity(self.kwh, self.eq)

    def as_dict(self, frequency: Frequency) -> dict[str, Any]:
        """Return the row with its numbers rounded for JSON or CSV."""
        return {
            "area": self.area,
            "periodo": period_label(self.start, frequency),
            "desde": self.start.isoformat(),
            "hasta": self.end.isoformat(),
            "dias": self.days,
            "dias_medidos": self.measured_days,
            "cobertura": json_number(self.coverage, COVERAGE_PLACES),
            "kwh": json_number(self.kwh, KWH_PLACES),
            "eq": json_number(self.eq, KWH_PLACES),
            "kwh_por_eq": json_number(self.kwh_per_eq, RATIO_PLACES),
        }


@dataclass(frozen=True, slots=True)
class AreaCross:
    """The cross of one area over a range.

    Attributes:
        energy: the energy of the area, with its meters and frozen counters.
        procesos: the battery processes whose EQ the energy is set against.
        rows: one row per day or month.
        total: the whole range.
        reconciled_months: the months whose daily EQ was checked against the month.
        eq_mismatches: how many driver-months of EQ did not add up.
    """

    energy: AreaEnergy
    procesos: tuple[str, ...]
    rows: tuple[CrossRow, ...]
    total: CrossRow
    reconciled_months: tuple[date, ...] = ()
    eq_mismatches: int = 0

    @property
    def area(self) -> str:
        """The plant area."""
        return self.energy.area


@dataclass(frozen=True, slots=True)
class EnergyCrossSeries:
    """The cross of every area asked for, over one range.

    Attributes:
        period: the days, both ends included.
        frequency: ``day`` or ``month``.
        areas: one cross per area, in the order asked.
    """

    period: DateRange
    frequency: Frequency
    areas: tuple[AreaCross, ...]

    @property
    def eq_mismatches(self) -> int:
        """How many driver-months of EQ did not add up, in every area."""
        return sum(area.eq_mismatches for area in self.areas)


def cross_rows(
    energy: AreaEnergy,
    eq_days: Mapping[date, PeriodTotal],
    procesos: Sequence[str],
    frequency: Frequency,
) -> tuple[tuple[CrossRow, ...], CrossRow]:
    """Pair the energy of an area with the EQ of the same days.

    Args:
        energy: the energy of the area.
        eq_days: the EQ of the area's processes on each day of the range.
        procesos: those processes, in report order.
        frequency: ``day`` or ``month``.

    Returns:
        The rows and the total of the range.
    """

    def row(span: EnergySpan) -> CrossRow:
        by_process = dict.fromkeys(procesos, _ZERO)
        for offset in range(span.days):
            total = eq_days.get(span.start + timedelta(days=offset))
            if total is not None:
                for name in procesos:
                    by_process[name] += total.by_process.get(name, _ZERO)
        return CrossRow(
            area=energy.area,
            start=span.start,
            end=span.end,
            kwh=span.kwh,
            coverage=span.coverage,
            measured_days=span.measured_days,
            eq=sum(by_process.values(), start=_ZERO),
            eq_by_process=by_process,
        )

    spans = energy.days if frequency == "day" else energy.months
    return tuple(row(span) for span in spans), row(energy.total)


# =============================================================================
# Output
# =============================================================================
def _amount(value: Decimal | None, places: int = 2) -> str:
    if value is None:
        return "-"
    # Half up, as the workbook rounds; format() alone would round half to even.
    step = _ONE.scaleb(-places)
    return f"{value.quantize(step, rounding=ROUND_HALF_UP):,.{places}f}"


def _percent(value: Decimal) -> str:
    return f"{(value * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def _local(instant: datetime, zone: tzinfo) -> str:
    return instant.astimezone(zone).strftime("%Y-%m-%d %H:%M")


def describe_cross(series: EnergyCrossSeries) -> list[str]:
    """Render a cross as a table per area, with its frozen counters and coverage."""
    unit = "day" if series.frequency == "day" else "month"
    lines = [
        f"kWh against equivalent batteries by {unit}, "
        f"{series.period.start.isoformat()} to {series.period.end.isoformat()} (live EQ)",
    ]
    for area in series.areas:
        energy = area.energy
        count = len(energy.meters)
        meters = f" ({count} meter{'' if count == 1 else 's'})" if count else ""
        lines += [
            "",
            f"{area.area}: {energy.source}{meters} against {' + '.join(area.procesos)}",
            f"{unit:<12}{'kWh':>16}{'coverage':>10}{'EQ':>14}{'kWh/EQ':>10}",
        ]
        for item in (*area.rows, area.total):
            label = "total" if item is area.total else period_label(item.start, series.frequency)
            lines.append(
                f"{label:<12}{_amount(item.kwh):>16}{_percent(item.coverage):>10}"
                f"{_amount(item.eq):>14}{_amount(item.kwh_per_eq):>10}"
            )
        total = area.total
        if total.measured_days is not None:
            lines.append(
                f"Days with a measured kWh: {total.measured_days} of {total.days}. "
                "The total is the counter difference over the range, so a frozen counter "
                "keeps its energy there."
            )
        for run in energy.frozen:
            lines.append(
                f"Frozen counter, left out of its days: {run.meter} from "
                f"{_local(run.start, energy.zone)} to {_local(run.end, energy.zone)} local "
                f"({_amount(run.hours, 0)} h), then a jump of {_amount(run.jump_kwh)} kWh"
            )
        if area.reconciled_months:
            months = ", ".join(month_label(month) for month in area.reconciled_months)
            if area.eq_mismatches:
                lines.append(f"EQ: {area.eq_mismatches} driver-months of {months} do not add up.")
            else:
                lines.append(f"EQ: the days add up to gold.v_resumen_equ exactly: {months}.")
    return lines


def cross_json(series: EnergyCrossSeries) -> dict[str, Any]:
    """Return a cross as a JSON-ready dictionary."""
    return {
        "rango": {
            "desde": series.period.start.isoformat(),
            "hasta": series.period.end.isoformat(),
        },
        "frecuencia": series.frequency,
        "areas": [
            {
                "area": area.area,
                "fuente": area.energy.source,
                "resolucion": area.energy.resolution,
                "medidores": list(area.energy.meters),
                "procesos": list(area.procesos),
                "filas": [
                    {
                        **item.as_dict(series.frequency),
                        "eq_procesos": {
                            name: json_number(value, KWH_PLACES)
                            for name, value in item.eq_by_process.items()
                        },
                    }
                    for item in area.rows
                ],
                "total": area.total.as_dict(series.frequency),
                "contadores_congelados": [
                    {
                        "medidor": run.meter,
                        "desde": run.start.isoformat(),
                        "hasta": run.end.isoformat(),
                        "horas": json_number(run.hours, KWH_PLACES),
                        "salto_kwh": json_number(run.jump_kwh, KWH_PLACES),
                        "limite_kwh_hora": json_number(run.limit_kwh_per_hour, KWH_PLACES),
                    }
                    for run in area.energy.frozen
                ],
                "conciliacion_eq": {
                    "meses": [month_label(month) for month in area.reconciled_months],
                    "diferencias": area.eq_mismatches,
                },
            }
            for area in series.areas
        ],
    }


def cross_csv_rows(series: EnergyCrossSeries) -> list[list[Any]]:
    """Return the rows of a cross for CSV, header first."""
    rows: list[list[Any]] = [list(CROSS_FIELDS)]
    for area in series.areas:
        for item in area.rows:
            values = item.as_dict(series.frequency)
            rows.append(["" if values[key] is None else values[key] for key in CROSS_FIELDS])
    return rows


__all__ = [
    "CROSS_FIELDS",
    "FROZEN_MIN_HOURS",
    "FY_START_MONTH",
    "OPERATING_KWH_PER_HOUR",
    "PHYSICAL_LIMIT_FACTOR",
    "AreaCross",
    "AreaEnergy",
    "CounterEnergy",
    "CounterReading",
    "CrossRow",
    "EnergyCrossSeries",
    "EnergySpan",
    "FrozenRun",
    "Interval",
    "MeterDay",
    "classify_counters",
    "counter_area_energy",
    "cross_csv_rows",
    "cross_json",
    "cross_rows",
    "daily_energy",
    "describe_cross",
    "energy_intensity",
    "fiscal_label",
    "fiscal_month",
    "fiscal_year",
    "month_spans",
]
