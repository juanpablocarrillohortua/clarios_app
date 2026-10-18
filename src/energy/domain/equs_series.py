"""The equivalent batteries of EQUS BATT as a series, by day or by month.

Layer: domain

Responsibility:
    ``gold.v_resumen_equ`` answers the equivalent batteries (EQ) of each driver
    in each month, and ``gold.v_resumen_equ_diario`` the same for each day. This
    module holds what a series of them is once the database has answered: the
    rows, the totals of each day or month by process, the check that the days of
    a whole month add up to the month, and the text, JSON and CSV the command
    prints.

    Only the drivers that add to EQUS BATT belong to a series: the battery
    processes OREC, ENSAMBLE, FORMACION, DECORADO and SHIPPING, as
    ``silver.factores_equ`` marks them. Which driver is which, and the process
    and plant area of each, come from the database; nothing here names one.

Depends on:
    ``energy.domain.equs_gold``, standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, Final, Literal

from energy.domain.equs_gold import (
    EQ_PLACES,
    FACTOR_PLACES,
    QUANTITY_PLACES,
    TOTAL_BATT,
    DateRange,
    VersionMonth,
    json_number,
    month_label,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

Frequency = Literal["day", "month"]
FREQUENCIES: Final[tuple[Frequency, ...]] = ("day", "month")

# The label of the total when a filter leaves some battery processes out.
TOTAL_SELECTED: Final = "total"

# The columns of one row of a series, in CSV order.
SERIES_FIELDS: Final = (
    "periodo",
    "version",
    "proceso",
    "area_planta",
    "cuenta_stats",
    "cantidad",
    "factor",
    "equs",
)

_ZERO = Decimal(0)
_CENT = Decimal("0.01")


# =============================================================================
# What the database answers
# =============================================================================
@dataclass(frozen=True, slots=True)
class DailyDriverAmount:
    """What one driver summed on one day, and its EQ.

    Attributes:
        day: the accounting date.
        month: the first day of its month.
        version: the version applied, the one in force in the month.
        cuenta_stats: the driver.
        cantidad: the quantity.
        equs: the equivalent batteries.
    """

    day: date
    month: date
    version: int
    cuenta_stats: str
    cantidad: Decimal
    equs: Decimal


@dataclass(frozen=True, slots=True)
class ReconciledAmount:
    """One driver of one month, as the month gives it and as its days add up.

    Attributes:
        month: the first day of the month.
        version: the version in force in the month.
        cuenta_stats: the driver.
        monthly_cantidad: the quantity of ``v_resumen_equ``; ``None`` if it has no row.
        daily_cantidad: the sum of the days; ``None`` if no day has a row.
        monthly_equs: the EQ of ``v_resumen_equ``.
        daily_equs: the sum of the days' EQ.
        equal: both quantities and both EQ are equal, digit for digit.
    """

    month: date
    version: int
    cuenta_stats: str
    monthly_cantidad: Decimal | None
    daily_cantidad: Decimal | None
    monthly_equs: Decimal | None
    daily_equs: Decimal | None
    equal: bool


# =============================================================================
# The series
# =============================================================================
@dataclass(frozen=True, slots=True)
class SeriesRow:
    """The EQ of one battery driver in one day or month.

    Attributes:
        start: the day, or the first day of the month.
        version: the version applied.
        proceso: the process of the driver.
        area_planta: the plant area of the process, if it has one.
        cuenta_stats: the driver.
        cantidad: the quantity.
        factor: the EQ of one unit.
        equs: the equivalent batteries.
    """

    start: date
    version: int
    proceso: str
    area_planta: str | None
    cuenta_stats: str
    cantidad: Decimal
    factor: Decimal
    equs: Decimal

    def as_dict(self, frequency: Frequency) -> dict[str, Any]:
        """Return the row with its numbers rounded for JSON or CSV."""
        return {
            "periodo": period_label(self.start, frequency),
            "version": self.version,
            "proceso": self.proceso,
            "area_planta": self.area_planta,
            "cuenta_stats": self.cuenta_stats,
            "cantidad": json_number(self.cantidad, QUANTITY_PLACES),
            "factor": json_number(self.factor, FACTOR_PLACES),
            "equs": json_number(self.equs, EQ_PLACES),
        }


@dataclass(frozen=True, slots=True)
class PeriodTotal:
    """The EQ of one day or month, by process and in all.

    Attributes:
        start: the day, or the first day of the month.
        by_process: the EQ of each process shown, 0 when it produced nothing.
        total: the sum of the processes shown.
    """

    start: date
    by_process: dict[str, Decimal]
    total: Decimal


@dataclass(frozen=True, slots=True)
class BatteryEquivalents:
    """The EQ of the battery processes over a range, by day or by month.

    Attributes:
        period: the days asked for, both ends included.
        frequency: ``day`` or ``month``.
        procesos: the processes shown, in the order of the report.
        filtered: whether a filter left some battery process out.
        versions: the version in force in each month of the range.
        rows: one row per driver and day (or month) that produced something.
        reconciliation: for the whole months of a daily series, each driver as
            the month gives it and as its days add up. Empty for a monthly series.
    """

    period: DateRange
    frequency: Frequency
    procesos: tuple[str, ...]
    filtered: bool
    versions: tuple[VersionMonth, ...]
    rows: tuple[SeriesRow, ...]
    reconciliation: tuple[ReconciledAmount, ...] = ()

    @property
    def total_label(self) -> str:
        """``EQUS BATT`` when every battery process is shown, ``total`` otherwise."""
        return TOTAL_SELECTED if self.filtered else TOTAL_BATT

    @property
    def reconciled_months(self) -> tuple[date, ...]:
        """The months whose days were checked against the month."""
        return tuple(sorted({item.month for item in self.reconciliation}))

    @property
    def mismatches(self) -> tuple[ReconciledAmount, ...]:
        """The drivers whose days do not add up to their month."""
        return tuple(item for item in self.reconciliation if not item.equal)


def period_starts(period: DateRange, frequency: Frequency) -> tuple[date, ...]:
    """Return the first day of every day or month a range touches, in order."""
    if frequency == "month":
        return period.months
    return tuple(period.start + timedelta(days=offset) for offset in range(period.days))


def period_label(start: date, frequency: Frequency) -> str:
    """Return a day as ``YYYY-MM-DD`` and a month as ``YYYY-MM``."""
    return month_label(start) if frequency == "month" else start.isoformat()


def totals(series: BatteryEquivalents) -> tuple[PeriodTotal, ...]:
    """Add up each day or month by process.

    Every day or month of the range appears, at 0 where nothing was produced,
    as the date-range functions list every driver at 0.
    """
    sums: dict[date, dict[str, Decimal]] = {
        start: dict.fromkeys(series.procesos, _ZERO)
        for start in period_starts(series.period, series.frequency)
    }
    for row in series.rows:
        sums[row.start][row.proceso] += row.equs
    return tuple(
        PeriodTotal(start, by_process, sum(by_process.values(), start=_ZERO))
        for start, by_process in sums.items()
    )


# =============================================================================
# Output
# =============================================================================
def _eq(value: Decimal) -> str:
    # Half up, as the workbook rounds; format() alone would round half to even.
    return f"{value.quantize(_CENT, rounding=ROUND_HALF_UP):,.2f}"


def describe_series(series: BatteryEquivalents) -> list[str]:
    """Render a series as a table: one line per day or month, one column per process."""
    unit = "day" if series.frequency == "day" else "month"
    width = max(12, *(len(name) + 2 for name in series.procesos))
    header = f"{unit:<12}" + "".join(f"{name:>{width}}" for name in series.procesos)
    lines = [
        f"Equivalent batteries by {unit}, "
        f"{series.period.start.isoformat()} to {series.period.end.isoformat()} (live)",
        "",
        header + f"{series.total_label:>{width + 2}}",
    ]
    grand: dict[str, Decimal] = dict.fromkeys(series.procesos, _ZERO)
    for total in totals(series):
        for name, value in total.by_process.items():
            grand[name] += value
        lines.append(
            f"{period_label(total.start, series.frequency):<12}"
            + "".join(f"{_eq(total.by_process[name]):>{width}}" for name in series.procesos)
            + f"{_eq(total.total):>{width + 2}}"
        )
    lines.append(
        f"{'total':<12}"
        + "".join(f"{_eq(grand[name]):>{width}}" for name in series.procesos)
        + f"{_eq(sum(grand.values(), start=_ZERO)):>{width + 2}}"
    )
    return [*lines, "", *describe_reconciliation(series)]


def describe_reconciliation(series: BatteryEquivalents) -> list[str]:
    """Say whether the days of each whole month add up to the month."""
    if series.frequency != "day":
        return []
    months = ", ".join(month_label(month) for month in series.reconciled_months)
    if not months:
        return ["No whole month in the range, so the days were not checked against a month."]
    if not series.mismatches:
        return [
            f"The days add up to gold.v_resumen_equ exactly: {months}, "
            f"{len(series.reconciliation)} driver-months compared."
        ]
    lines = [
        f"{len(series.mismatches)} of {len(series.reconciliation)} driver-months of {months} "
        "do not add up to gold.v_resumen_equ:"
    ]
    lines += [
        f"  {month_label(item.month)}  {item.cuenta_stats:<17} month {item.monthly_equs}  "
        f"days {item.daily_equs}"
        for item in series.mismatches
    ]
    return lines


def _reconciled(item: ReconciledAmount) -> dict[str, Any]:
    return {
        "mes": month_label(item.month),
        "version": item.version,
        "cuenta_stats": item.cuenta_stats,
        "cantidad_mes": json_number(item.monthly_cantidad, QUANTITY_PLACES),
        "cantidad_dias": json_number(item.daily_cantidad, QUANTITY_PLACES),
        "equs_mes": json_number(item.monthly_equs, EQ_PLACES),
        "equs_dias": json_number(item.daily_equs, EQ_PLACES),
    }


def series_json(series: BatteryEquivalents) -> dict[str, Any]:
    """Return a series as a JSON-ready dictionary."""
    payload: dict[str, Any] = {
        "rango": {
            "desde": series.period.start.isoformat(),
            "hasta": series.period.end.isoformat(),
            "meses_parciales": [month_label(m) for m in series.period.partial_months],
        },
        "frecuencia": series.frequency,
        "procesos": list(series.procesos),
        "total": series.total_label,
        "versiones": [
            {"mes": month_label(item.month), "version": item.version, "nombre": item.name}
            for item in series.versions
        ],
        "periodos": [
            {
                "periodo": period_label(total.start, series.frequency),
                "procesos": {
                    name: json_number(value, EQ_PLACES) for name, value in total.by_process.items()
                },
                "total": json_number(total.total, EQ_PLACES),
            }
            for total in totals(series)
        ],
        "filas": [row.as_dict(series.frequency) for row in series.rows],
    }
    if series.frequency == "day":
        payload["conciliacion"] = {
            "meses": [month_label(month) for month in series.reconciled_months],
            "filas_comparadas": len(series.reconciliation),
            "diferencias": [_reconciled(item) for item in series.mismatches],
        }
    return payload


def series_csv_rows(series: BatteryEquivalents) -> list[list[Any]]:
    """Return the rows of a series for CSV, header first."""
    rows: list[list[Any]] = [list(SERIES_FIELDS)]
    for row in series.rows:
        values = row.as_dict(series.frequency)
        rows.append(["" if values[key] is None else values[key] for key in SERIES_FIELDS])
    return rows


def battery_rows(
    amounts: Sequence[tuple[date, int, str, Decimal, Decimal]],
    drivers: dict[tuple[int, str], tuple[str, str | None, Decimal]],
    procesos: Sequence[str],
) -> tuple[SeriesRow, ...]:
    """Keep the amounts of the battery drivers of the processes shown.

    Args:
        amounts: (start, version, driver, quantity, EQ) as the database gave them.
        drivers: (version, driver) -> (process, plant area, factor) of each
            driver that adds to EQUS BATT.
        procesos: the processes shown.

    Returns:
        The rows that produced something, in date order then the order given.
    """
    shown = set(procesos)
    rows = []
    for start, version, cuenta, cantidad, equs in amounts:
        driver = drivers.get((version, cuenta))
        if driver is None or driver[0] not in shown or (cantidad == 0 and equs == 0):
            continue
        proceso, area, factor = driver
        rows.append(SeriesRow(start, version, proceso, area, cuenta, cantidad, factor, equs))
    order = {name: position for position, name in enumerate(procesos)}
    return tuple(sorted(rows, key=lambda row: (row.start, order[row.proceso], row.cuenta_stats)))


__all__ = [
    "FREQUENCIES",
    "SERIES_FIELDS",
    "TOTAL_SELECTED",
    "BatteryEquivalents",
    "DailyDriverAmount",
    "Frequency",
    "PeriodTotal",
    "ReconciledAmount",
    "SeriesRow",
    "battery_rows",
    "describe_reconciliation",
    "describe_series",
    "period_label",
    "period_starts",
    "series_csv_rows",
    "series_json",
    "totals",
]
