"""Equivalent batteries (EQUS) Gold: periods, the report, the close, as plain data.

Layer: domain

Responsibility:
    The Gold layer of the equivalent-batteries calculation lives in the
    database: views apply the equation stored in Silver, and functions answer
    it for any date range. This module holds what is left once the database has
    answered, all of it free of business rules:

    * reading and checking the periods people ask for (a month ``2026-09``, a
      range of days, both ends included);
    * the rows the database returns, as typed records;
    * :func:`nest_report`, which arranges those rows the way sheet RESUMEN EQU
      of the workbook does (plants, processes, drivers, then EQUS POLY and the
      informative drivers), adds them up and rounds them for JSON;
    * :func:`column_h`, which flattens that report into the rows of column H;
    * the text a close prints.

    Which rows feed a driver, its factor, the order of the sections and their
    labels all come from the database. Nothing here names a driver, a plant or
    a category. Totals are added from unrounded values and rounded once, at the
    end.

Depends on:
    ``energy.domain.exceptions``, standard library only.
"""

from __future__ import annotations

import calendar
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any, Final, Literal

from energy.domain.exceptions import InvalidPeriodError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

ReportSource = Literal["live", "closed"]
REPORT_SOURCES: Final[tuple[ReportSource, ...]] = ("live", "closed")

# Decimal places of the numbers in the JSON report.
QUANTITY_PLACES: Final = 6
FACTOR_PLACES: Final = 10
EQ_PLACES: Final = 6

# How far a live number may drift from the frozen close before it is reported.
CLOSE_TOLERANCE: Final = Decimal("0.01")

_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ZERO = Decimal(0)


# =============================================================================
# Periods
# =============================================================================
def parse_month(text: str) -> date:
    """Read a month written ``YYYY-MM``.

    Args:
        text: the month, for example ``2026-09``.

    Returns:
        The first day of the month.

    Raises:
        InvalidPeriodError: if the text is not a month.
    """
    match = _MONTH.match(text.strip())
    if match is None or not 1 <= int(match.group(2)) <= 12:  # noqa: PLR2004 - months of a year
        raise InvalidPeriodError("a month is written YYYY-MM, for example 2026-09", value=text)
    return date(int(match.group(1)), int(match.group(2)), 1)


def parse_day(text: str, *, name: str = "date") -> date:
    """Read a date written ``YYYY-MM-DD``.

    Args:
        text: the date.
        name: what the date is, for the error message.

    Returns:
        The date.

    Raises:
        InvalidPeriodError: if the text is not a date.
    """
    candidate = text.strip()
    try:
        if not _DAY.match(candidate):
            raise ValueError(candidate)
        return date.fromisoformat(candidate)
    except ValueError:
        raise InvalidPeriodError(
            f"{name} is written YYYY-MM-DD, for example 2026-09-01", value=text
        ) from None


def month_label(month: date) -> str:
    """Return a month as ``YYYY-MM``."""
    return f"{month.year:04d}-{month.month:02d}"


def month_end(month: date) -> date:
    """Return the last day of the month a date falls in."""
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def _next_month(month: date) -> date:
    return month_end(month) + timedelta(days=1)


@dataclass(frozen=True, slots=True)
class DateRange:
    """A period of whole days, both ends included, as the reports compare it.

    Attributes:
        start: the first day.
        end: the last day.

    Raises:
        InvalidPeriodError: if the start is later than the end.
    """

    start: date
    end: date

    def __post_init__(self) -> None:
        """Refuse a range that ends before it starts."""
        if self.start > self.end:
            raise InvalidPeriodError(
                "the start is later than the end",
                start=self.start.isoformat(),
                end=self.end.isoformat(),
            )

    @classmethod
    def of_month(cls, month: date) -> DateRange:
        """Return the whole month a date falls in."""
        first = month.replace(day=1)
        return cls(first, month_end(first))

    @property
    def days(self) -> int:
        """How many days the range covers."""
        return (self.end - self.start).days + 1

    @property
    def months(self) -> tuple[date, ...]:
        """The first day of every month the range touches, in order."""
        months: list[date] = []
        month = self.start.replace(day=1)
        while month <= self.end:
            months.append(month)
            month = _next_month(month)
        return tuple(months)

    @property
    def partial_months(self) -> tuple[date, ...]:
        """The months the range touches without covering them whole."""
        return tuple(
            month for month in self.months if self.start > month or self.end < month_end(month)
        )


# =============================================================================
# What the database answers
# =============================================================================
@dataclass(frozen=True, slots=True)
class VersionMonth:
    """The version of the equation in force in one month.

    Attributes:
        month: the first day of the month.
        version: the version, or ``None`` when none covers the month.
        name: its name, for example ``STD FY26``.
    """

    month: date
    version: int | None
    name: str | None


@dataclass(frozen=True, slots=True)
class Driver:
    """One driver (cuenta STATS) of one version, with where the report shows it.

    Attributes:
        version: the version of the equation.
        version_name: its name.
        cuenta_stats: the code of the driver.
        descripcion: its description (column B of RESUMEN EQU).
        proceso: the process it belongs to.
        unidad: KG or UN.
        fuente: what it sums.
        estandar_por_bat: the standard per battery.
        multiplicador: the complexity multiplier.
        peso: the weight of its process in the conversion cost.
        factor: the EQ of one unit.
        suma_eq: whether it adds to EQUS BATT.
        fila: its row in RESUMEN EQU, if the layout lists it.
        planta: the plant label of its process (column F of RESUMEN EQU).
        area_planta: the plant area of its process, if it has one.
        orden: the position of its process in the report.
        peso_proceso: the weight of its whole process.
    """

    version: int
    version_name: str
    cuenta_stats: str
    descripcion: str | None
    proceso: str
    unidad: str
    fuente: str
    estandar_por_bat: Decimal
    multiplicador: Decimal
    peso: Decimal
    factor: Decimal
    suma_eq: bool
    fila: int | None
    planta: str
    area_planta: str | None
    orden: int
    peso_proceso: Decimal


@dataclass(frozen=True, slots=True)
class DriverAmount:
    """What one driver summed in one month of a range, and its EQ.

    Attributes:
        month: the first day of the month.
        version: the version applied.
        cuenta_stats: the driver.
        cantidad: the quantity.
        equs: the equivalent batteries.
    """

    month: date
    version: int
    cuenta_stats: str
    cantidad: Decimal
    equs: Decimal


@dataclass(frozen=True, slots=True)
class PolyAmount:
    """EQUS POLY of one plastic-injection category in one month of a range.

    Attributes:
        month: the first day of the month.
        cuenta: the account of the category, if Clasif_Poly gives one.
        categoria: the category, if Clasif_Poly lists the item.
        area_planta: the plant area EQUS POLY is reported under.
        piezas: the pieces produced.
        piezas_con_equ: the pieces of a category whose factor is not zero.
        factor_equ: the factor, when the whole category shares one.
        equs: the equivalent batteries.
        fila: the row of the account in RESUMEN EQU, if the layout lists it.
    """

    month: date
    cuenta: str | None
    categoria: str | None
    area_planta: str | None
    piezas: Decimal
    piezas_con_equ: Decimal
    factor_equ: Decimal | None
    equs: Decimal
    fila: int | None


@dataclass(frozen=True, slots=True)
class QualityCount:
    """How many rows one data-quality check returns for a period.

    Attributes:
        vista: the view of the check.
        descripcion: what it looks for.
        bloqueante: whether rows stop a monthly close.
        filas: how many rows it returned.
    """

    vista: str
    descripcion: str
    bloqueante: bool
    filas: int


@dataclass(frozen=True, slots=True)
class ClosedDifference:
    """A driver whose live EQ no longer equals the frozen close of its month.

    Attributes:
        month: the first day of the closed month.
        cuenta_stats: the driver.
        live: the EQ computed now.
        closed: the EQ the close froze.
    """

    month: date
    cuenta_stats: str
    live: Decimal
    closed: Decimal


@dataclass(frozen=True, slots=True)
class ReportData:
    """Everything a date-range report is made of, before it is arranged.

    Attributes:
        period: the range asked for.
        source: ``live`` (computed now) or ``closed`` (the frozen close).
        generated_at: when the report was made, with its time zone.
        versions: the version in force in each month.
        drivers: every driver of every version that applies.
        amounts: what each driver summed, month by month.
        poly: EQUS POLY, month by month and category.
        quality: the data-quality counts for the range.
        closed_differences: live numbers that differ from a close.
        by_month: whether to add the month-by-month breakdown.
    """

    period: DateRange
    source: ReportSource
    generated_at: datetime
    versions: tuple[VersionMonth, ...]
    drivers: tuple[Driver, ...]
    amounts: tuple[DriverAmount, ...]
    poly: tuple[PolyAmount, ...]
    quality: tuple[QualityCount, ...]
    closed_differences: tuple[ClosedDifference, ...] = ()
    by_month: bool = False


# =============================================================================
# Numbers for JSON
# =============================================================================
def json_number(value: Decimal | None, places: int) -> int | float | None:
    """Round a value for the JSON report.

    Half goes up, as the workbook rounds. A whole number is written as an
    integer, so ``1162129`` reads as the workbook shows it.

    Args:
        value: the value; ``None`` stays ``None``.
        places: the decimal places to keep.

    Returns:
        A JSON number, or ``None``.
    """
    if value is None:
        return None
    rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    if rounded == rounded.to_integral_value():
        return int(rounded)
    return float(rounded)


def _quantity(value: Decimal | None) -> int | float | None:
    return json_number(value, QUANTITY_PLACES)


def _term(value: Decimal | None) -> int | float | None:
    return json_number(value, FACTOR_PLACES)


def _eq(value: Decimal | None) -> int | float | None:
    return json_number(value, EQ_PLACES)


def _same(values: Iterable[Decimal]) -> Decimal | None:
    """Return the one value every version shares, or ``None`` when they differ."""
    distinct = set(values)
    return next(iter(distinct)) if len(distinct) == 1 else None


# =============================================================================
# Warnings
# =============================================================================
@dataclass(frozen=True, slots=True)
class ReportWarning:
    """A warning the report carries.

    Attributes:
        codigo: a stable code.
        mensaje: what it means, in English.
        meses: the months it concerns, if any.
    """

    codigo: str
    mensaje: str
    meses: tuple[str, ...] = field(default=())

    def as_dict(self) -> dict[str, Any]:
        """Return the warning as it appears in the JSON."""
        result: dict[str, Any] = {"codigo": self.codigo, "mensaje": self.mensaje}
        if self.meses:
            result["meses"] = list(self.meses)
        return result


def report_warnings(data: ReportData) -> tuple[ReportWarning, ...]:
    """List what a reader of the report must know before trusting its numbers.

    Args:
        data: the report's rows.

    Returns:
        The warnings, in a fixed order.
    """
    warnings: list[ReportWarning] = []

    partial = data.period.partial_months
    if partial:
        warnings.append(
            ReportWarning(
                "mes_parcial",
                "The range cuts a month. The workbook only ever reports whole months, "
                "so these figures have no counterpart in it.",
                tuple(month_label(month) for month in partial),
            )
        )

    versions = sorted({version.version for version in data.versions if version.version})
    if len(versions) > 1:
        warnings.append(
            ReportWarning(
                "varias_versiones",
                "The range spans more than one version of the equation "
                f"({', '.join(str(v) for v in versions)}): each month uses its own, and the "
                "factor of a driver is given per version.",
            )
        )

    blocking = [count for count in data.quality if count.bloqueante and count.filas]
    if blocking:
        warnings.append(
            ReportWarning(
                "calidad_bloqueante",
                "Blocking data-quality checks have rows in the range: "
                + ", ".join(f"{count.vista} ({count.filas})" for count in blocking)
                + ". A month with them cannot be closed without accepting the warnings.",
            )
        )

    if data.closed_differences:
        months = sorted({difference.month for difference in data.closed_differences})
        drivers = sorted({difference.cuenta_stats for difference in data.closed_differences})
        warnings.append(
            ReportWarning(
                "difiere_del_cierre",
                "The live figures of a closed month differ from its frozen close by more "
                f"than {CLOSE_TOLERANCE}: {', '.join(drivers)}. Silver changed after the "
                "close.",
                tuple(month_label(month) for month in months),
            )
        )

    if data.source == "closed":
        warnings.append(
            ReportWarning(
                "poly_en_vivo",
                "EQUS POLY is not frozen by the close, so it is computed from the live data.",
            )
        )
    return tuple(warnings)


# =============================================================================
# The report, arranged as RESUMEN EQU
# =============================================================================
def _order(driver: Driver) -> tuple[int, str]:
    """Sort by the row of RESUMEN EQU; a driver the layout does not list goes last."""
    return (driver.fila if driver.fila is not None else 10_000, driver.cuenta_stats)


def _by_month(
    amounts: Iterable[DriverAmount],
) -> dict[date, tuple[Decimal, Decimal]]:
    totals: dict[date, list[Decimal]] = defaultdict(lambda: [_ZERO, _ZERO])
    for amount in amounts:
        totals[amount.month][0] += amount.cantidad
        totals[amount.month][1] += amount.equs
    return {month: (pair[0], pair[1]) for month, pair in sorted(totals.items())}


@dataclass(slots=True)
class _Subsection:
    """One driver of the report while it is being added up."""

    latest: Driver
    versions: dict[int, Driver] = field(default_factory=dict)
    amounts: list[DriverAmount] = field(default_factory=list)

    @property
    def cantidad(self) -> Decimal:
        return sum((amount.cantidad for amount in self.amounts), _ZERO)

    @property
    def equs(self) -> Decimal:
        return sum((amount.equs for amount in self.amounts), _ZERO)

    def as_dict(self, *, several_versions: bool, by_month: bool) -> dict[str, Any]:
        driver = self.latest
        result: dict[str, Any] = {
            "fila": driver.fila,
            "cuenta_stats": driver.cuenta_stats,
            "descripcion": driver.descripcion,
            "unidad": driver.unidad,
            "fuente": driver.fuente,
            "cantidad": _quantity(self.cantidad),
        }
        if several_versions:
            # The factor of a range spanning versions is not one number.
            result |= {
                "estandar_por_bat": None,
                "multiplicador": None,
                "peso": None,
                "factor": None,
            }
            result["por_version"] = [
                {
                    "version": version,
                    "cantidad": _quantity(
                        sum((a.cantidad for a in self.amounts if a.version == version), _ZERO)
                    ),
                    "estandar_por_bat": _term(self.versions[version].estandar_por_bat),
                    "multiplicador": _term(self.versions[version].multiplicador),
                    "peso": _term(self.versions[version].peso),
                    "factor": _term(self.versions[version].factor),
                    "equs": _eq(sum((a.equs for a in self.amounts if a.version == version), _ZERO)),
                }
                for version in sorted(self.versions)
            ]
        else:
            result |= {
                "estandar_por_bat": _term(driver.estandar_por_bat),
                "multiplicador": _term(driver.multiplicador),
                "peso": _term(driver.peso),
                "factor": _term(driver.factor),
            }
        result["equs"] = _eq(self.equs)
        if by_month:
            result["por_mes"] = [
                {"mes": month_label(month), "cantidad": _quantity(cantidad), "equs": _eq(equs)}
                for month, (cantidad, equs) in _by_month(self.amounts).items()
            ]
        return result


def _months_of(amounts: Iterable[DriverAmount]) -> list[dict[str, Any]]:
    return [
        {"mes": month_label(month), "equs": _eq(equs)}
        for month, (_, equs) in _by_month(amounts).items()
    ]


def _poly(data: ReportData) -> dict[str, Any]:
    """Arrange EQUS POLY: every category with production, in RESUMEN EQU order."""
    categories: dict[tuple[str | None, str | None], list[PolyAmount]] = defaultdict(list)
    for amount in data.poly:
        categories[(amount.cuenta, amount.categoria)].append(amount)

    def order(key: tuple[str | None, str | None]) -> tuple[int, str, str]:
        fila = categories[key][0].fila
        return (fila if fila is not None else 10_000, key[0] or "", key[1] or "")

    subsections: list[dict[str, Any]] = []
    for key in sorted(categories, key=order):
        amounts = categories[key]
        factors = {amount.factor_equ for amount in amounts}
        subsection: dict[str, Any] = {
            "fila": amounts[0].fila,
            "cuenta": key[0],
            "categoria": key[1],
            "piezas": _quantity(sum((a.piezas for a in amounts), _ZERO)),
            "factor_equ": _term(factors.pop()) if len(factors) == 1 else None,
            "equs": _eq(sum((a.equs for a in amounts), _ZERO)),
        }
        if data.by_month:
            months: dict[date, list[Decimal]] = defaultdict(lambda: [_ZERO, _ZERO])
            for amount in amounts:
                months[amount.month][0] += amount.piezas
                months[amount.month][1] += amount.equs
            subsection["por_mes"] = [
                {"mes": month_label(month), "piezas": _quantity(pair[0]), "equs": _eq(pair[1])}
                for month, pair in sorted(months.items())
            ]
        subsections.append(subsection)

    areas = {amount.area_planta for amount in data.poly if amount.area_planta}
    return {
        "area_planta": areas.pop() if len(areas) == 1 else None,
        "piezas": _quantity(sum((a.piezas for a in data.poly), _ZERO)),
        "piezas_con_equ": _quantity(sum((a.piezas_con_equ for a in data.poly), _ZERO)),
        "equs": _eq(sum((a.equs for a in data.poly), _ZERO)),
        "subsecciones": subsections,
    }


def _subsections(data: ReportData, versions_used: Sequence[int]) -> list[_Subsection]:
    """Gather each driver's versions and amounts, in the order of RESUMEN EQU."""
    subsections: dict[str, _Subsection] = {}
    for driver in sorted(data.drivers, key=lambda d: d.version):
        if driver.version not in versions_used:
            continue
        current = subsections.get(driver.cuenta_stats)
        if current is None:
            current = subsections[driver.cuenta_stats] = _Subsection(latest=driver)
        current.latest = driver
        current.versions[driver.version] = driver
    for amount in data.amounts:
        if amount.cuenta_stats in subsections:
            subsections[amount.cuenta_stats].amounts.append(amount)
    return sorted(subsections.values(), key=lambda s: _order(s.latest))


def _plants(
    adding: Sequence[_Subsection], *, several_versions: bool, by_month: bool
) -> tuple[list[dict[str, Any]], Decimal]:
    """Arrange the drivers that add to EQUS BATT under their process and plant.

    Returns:
        The plants, and EQUS BATT added from unrounded values.
    """
    plants: dict[str, dict[str, list[_Subsection]]] = {}
    for subsection in sorted(adding, key=lambda s: (s.latest.orden, _order(s.latest))):
        plants.setdefault(subsection.latest.planta, {}).setdefault(
            subsection.latest.proceso, []
        ).append(subsection)

    def process_weight(members: Sequence[_Subsection]) -> Decimal | None:
        return _same(
            driver.peso_proceso for member in members for driver in member.versions.values()
        )

    plant_list: list[dict[str, Any]] = []
    equs_batt = _ZERO
    for plant, processes in plants.items():
        process_list: list[dict[str, Any]] = []
        plant_equs = _ZERO
        plant_weight: Decimal | None = _ZERO
        plant_amounts: list[DriverAmount] = []
        for proceso, members in processes.items():
            process_equs = sum((member.equs for member in members), _ZERO)
            weight = process_weight(members)
            plant_equs += process_equs
            plant_weight = None if weight is None or plant_weight is None else plant_weight + weight
            process_amounts = [amount for member in members for amount in member.amounts]
            plant_amounts += process_amounts
            process_list.append(
                {
                    "proceso": proceso,
                    "peso": _term(weight),
                    "equs": _eq(process_equs),
                    "subsecciones": [
                        member.as_dict(several_versions=several_versions, by_month=by_month)
                        for member in members
                    ],
                }
                | ({"por_mes": _months_of(process_amounts)} if by_month else {})
            )
        equs_batt += plant_equs
        plant_list.append(
            {
                "planta": plant,
                "area_planta": next(iter(processes.values()))[0].latest.area_planta,
                "peso": _term(plant_weight),
                "equs": _eq(plant_equs),
                "procesos": process_list,
            }
            | ({"por_mes": _months_of(plant_amounts)} if by_month else {})
        )
    return plant_list, equs_batt


def _informative(
    subsections: Sequence[_Subsection], *, several_versions: bool, by_month: bool
) -> list[dict[str, Any]]:
    """Return the drivers that do not add to EQUS BATT: their quantity only."""
    keys = ("fila", "cuenta_stats", "descripcion", "unidad", "cantidad") + (
        ("por_mes",) if by_month else ()
    )
    informative: list[dict[str, Any]] = []
    for subsection in subsections:
        if not subsection.latest.suma_eq:
            entry = subsection.as_dict(several_versions=several_versions, by_month=by_month)
            informative.append({key: entry[key] for key in keys})
    return informative


def _report_months(data: ReportData, adding: Sequence[_Subsection]) -> list[dict[str, Any]]:
    """Return EQUS BATT and EQUS POLY of each month of the range."""
    batt = _by_month(amount for subsection in adding for amount in subsection.amounts)
    poly: dict[date, Decimal] = defaultdict(lambda: _ZERO)
    for category in data.poly:
        poly[category.month] += category.equs
    return [
        {
            "mes": month_label(month),
            "equs_batt": _eq(batt.get(month, (_ZERO, _ZERO))[1]),
            "equs_poly": _eq(poly.get(month, _ZERO)),
        }
        for month in data.period.months
    ]


def nest_report(data: ReportData) -> dict[str, Any]:
    """Arrange a report's rows the way sheet RESUMEN EQU does, ready for JSON.

    Plants in the order of their first process, processes in their order, and
    each driver (a subsection) in its row of RESUMEN EQU. Every driver of every
    version that applies appears, with 0 when nothing fed it. The drivers that
    add to EQUS BATT go under their plant; the others are the informative
    drivers. Every total is added from unrounded values.

    Args:
        data: the report's rows.

    Returns:
        The report as a dictionary of JSON values.
    """
    versions_used = sorted({version.version for version in data.versions if version.version})
    several_versions = len(versions_used) > 1
    ordered = _subsections(data, versions_used)
    adding = [subsection for subsection in ordered if subsection.latest.suma_eq]
    plants, equs_batt = _plants(adding, several_versions=several_versions, by_month=data.by_month)

    version_names = {version.version: version.name for version in data.versions}
    report: dict[str, Any] = {
        "rango": {
            "desde": data.period.start.isoformat(),
            "hasta": data.period.end.isoformat(),
            "dias": data.period.days,
            "meses": [month_label(month) for month in data.period.months],
        },
        "fuente": data.source,
        "generado_en": data.generated_at.isoformat(timespec="seconds"),
        "versiones": [
            {
                "version": version,
                "nombre": version_names.get(version),
                "meses": [
                    month_label(item.month) for item in data.versions if item.version == version
                ],
            }
            for version in versions_used
        ],
        "plantas": plants,
        "equs_batt": _eq(equs_batt),
        "equs_poly": _poly(data),
        "informativos": _informative(
            ordered, several_versions=several_versions, by_month=data.by_month
        ),
        "calidad_datos": {count.vista: count.filas for count in data.quality},
        "advertencias": [warning.as_dict() for warning in report_warnings(data)],
    }
    if data.by_month:
        report["por_mes"] = _report_months(data, adding)
    return report


# =============================================================================
# Column H of RESUMEN EQU
# =============================================================================
ColumnHKind = Literal["cuenta", "total", "poly"]


@dataclass(frozen=True, slots=True)
class ColumnHRow:
    """One row of the column-H table of a month.

    Attributes:
        tipo: ``cuenta`` for a driver, ``total`` for a total, ``poly`` for an
            EQUS POLY category.
        fila: the row of RESUMEN EQU, when the layout lists it.
        nombre: the cuenta STATS, the account of a POLY category, or the name
            of a total.
        proceso: the process of a driver, or the process a total sums.
        planta: the plant label, or the plant area of EQUS POLY.
        categoria: the plastic-injection category of a POLY row.
        cantidad: column D (pieces for a POLY row).
        factor: column G.
        equs: column H.
    """

    tipo: ColumnHKind
    fila: int | None
    nombre: str
    proceso: str | None
    planta: str | None
    categoria: str | None
    cantidad: int | float | None
    factor: int | float | None
    equs: int | float | None

    def as_dict(self) -> dict[str, Any]:
        """Return the row as JSON values."""
        return {
            "tipo": self.tipo,
            "fila": self.fila,
            "nombre": self.nombre,
            "proceso": self.proceso,
            "planta": self.planta,
            "categoria": self.categoria,
            "cantidad": self.cantidad,
            "factor": self.factor,
            "equs": self.equs,
        }


COLUMN_H_FIELDS: Final = (
    "tipo",
    "fila",
    "nombre",
    "proceso",
    "planta",
    "categoria",
    "cantidad",
    "factor",
    "equs",
)
TOTAL_BATT: Final = "EQUS BATT"
TOTAL_POLY: Final = "EQUS POLY"


def column_h(report: dict[str, Any]) -> list[ColumnHRow]:
    """Flatten a report into the rows of column H of RESUMEN EQU.

    The drivers in the order of the workbook's rows, then the totals the
    workbook keeps in rows 36 to 45 (EQUS BATT, each plant, and the processes of
    a plant that has more than one), then the EQUS POLY categories and their
    total.

    Args:
        report: a report built by :func:`nest_report`.

    Returns:
        The rows, in order.
    """
    drivers: list[ColumnHRow] = []
    for plant in report["plantas"]:
        for process in plant["procesos"]:
            drivers.extend(
                ColumnHRow(
                    "cuenta",
                    item["fila"],
                    item["cuenta_stats"],
                    process["proceso"],
                    plant["planta"],
                    None,
                    item["cantidad"],
                    item["factor"],
                    item["equs"],
                )
                for item in process["subsecciones"]
            )
    drivers.extend(
        ColumnHRow(
            "cuenta",
            item["fila"],
            item["cuenta_stats"],
            None,
            None,
            None,
            item["cantidad"],
            None,
            None,
        )
        for item in report["informativos"]
    )
    drivers.sort(key=lambda row: (row.fila if row.fila is not None else 10_000, row.nombre))

    totals = [
        ColumnHRow("total", None, TOTAL_BATT, None, None, None, None, None, report["equs_batt"])
    ]
    for plant in report["plantas"]:
        totals.append(
            ColumnHRow(
                "total",
                None,
                plant["planta"],
                None,
                plant["planta"],
                None,
                None,
                None,
                plant["equs"],
            )
        )
    for plant in report["plantas"]:
        if len(plant["procesos"]) > 1:
            totals.extend(
                ColumnHRow(
                    "total", None, process["proceso"], process["proceso"], plant["planta"],
                    None, None, None, process["equs"],
                )
                for process in plant["procesos"]
            )  # fmt: skip

    poly = report["equs_poly"]
    categories = [
        ColumnHRow(
            "poly",
            item["fila"],
            item["cuenta"] or "",
            None,
            poly["area_planta"],
            item["categoria"],
            item["piezas"],
            item["factor_equ"],
            item["equs"],
        )
        for item in poly["subsecciones"]
    ]
    categories.append(
        ColumnHRow("total", None, TOTAL_POLY, None, poly["area_planta"], None,
                   poly["piezas_con_equ"], None, poly["equs"])
    )  # fmt: skip
    return drivers + totals + categories


# =============================================================================
# Refresh and close
# =============================================================================
@dataclass(frozen=True, slots=True)
class RefreshStep:
    """One step of a refresh and how long it took.

    Attributes:
        name: what was done.
        seconds: how long it took.
        detail: anything worth saying about it.
    """

    name: str
    seconds: float
    detail: str = ""


@dataclass(frozen=True, slots=True)
class FrozenMonth:
    """What a close wrote.

    Attributes:
        month: the first day of the closed month.
        cierre_id: the close's row in ``gold.cierre_mes``.
        rows: the driver rows frozen.
        reclosed: whether it replaced an earlier close.
        replaced: the rows of the earlier close kept in the history.
        accepted_warnings: whether blocking checks had rows and were accepted.
    """

    month: date
    cierre_id: int
    rows: int
    reclosed: bool
    replaced: int
    accepted_warnings: bool


@dataclass(frozen=True, slots=True)
class CloseSummary:
    """A closed month, as the close prints it.

    Attributes:
        frozen: what the close wrote.
        quality: the data-quality counts the close saw.
        processes: the frozen EQ of each process that adds to EQUS BATT.
        equs_batt: the frozen EQUS BATT.
        equs_poly: EQUS POLY of the month, computed live: it is not frozen.
        previous_month: the closed month before it, if any.
        previous_processes: the EQ of each process in that month.
        previous_equs_batt: EQUS BATT of that month.
    """

    frozen: FrozenMonth
    quality: tuple[QualityCount, ...]
    processes: tuple[tuple[str, Decimal], ...]
    equs_batt: Decimal
    equs_poly: Decimal
    previous_month: date | None = None
    previous_processes: tuple[tuple[str, Decimal], ...] = ()
    previous_equs_batt: Decimal | None = None


def _variation(current: Decimal, previous: Decimal | None) -> str:
    if previous is None:
        return ""
    if previous == 0:
        return "   (no EQ the month before)"
    change = (current - previous) / abs(previous) * 100
    return f"   ({change:+.1f} % against {previous:,.2f})"


def describe_quality(quality: Sequence[QualityCount]) -> list[str]:
    """Return the data-quality counts as lines of text."""
    lines = ["Data-quality checks"]
    for count in quality:
        flag = "BLOCKING" if count.bloqueante and count.filas else ""
        lines.append(f"  {count.vista:<22} {count.filas:>8,}  {flag:<8}  {count.descripcion}")
    return lines


def describe_close(summary: CloseSummary) -> list[str]:
    """Return a close as lines of text.

    Args:
        summary: the close.

    Returns:
        The EQ of each process, EQUS BATT and EQUS POLY of the closed month, with
        the change against the previous closed month.
    """
    frozen = summary.frozen
    action = "Reclosed" if frozen.reclosed else "Closed"
    lines = [
        f"{action} {month_label(frozen.month)}: {frozen.rows} driver rows frozen "
        f"(close {frozen.cierre_id})."
    ]
    if frozen.reclosed:
        lines.append(f"  The {frozen.replaced} rows it replaced are kept in the history.")
    if frozen.accepted_warnings:
        lines.append("  Blocking data-quality checks had rows; the warnings were accepted.")
    previous = dict(summary.previous_processes)
    reference = (
        f"previous closed month {month_label(summary.previous_month)}"
        if summary.previous_month
        else "no previous closed month"
    )
    lines.append("")
    lines.append(f"EQ by process ({reference})")
    for proceso, equs in summary.processes:
        lines.append(f"  {proceso:<12} {equs:>14,.2f}{_variation(equs, previous.get(proceso))}")
    lines.append(
        f"  {TOTAL_BATT:<12} {summary.equs_batt:>14,.2f}"
        f"{_variation(summary.equs_batt, summary.previous_equs_batt)}"
    )
    lines.append(f"  {TOTAL_POLY:<12} {summary.equs_poly:>14,.2f}   (live, not frozen)")
    return lines


__all__ = [
    "CLOSE_TOLERANCE",
    "COLUMN_H_FIELDS",
    "EQ_PLACES",
    "FACTOR_PLACES",
    "QUANTITY_PLACES",
    "REPORT_SOURCES",
    "TOTAL_BATT",
    "TOTAL_POLY",
    "CloseSummary",
    "ClosedDifference",
    "ColumnHKind",
    "ColumnHRow",
    "DateRange",
    "Driver",
    "DriverAmount",
    "FrozenMonth",
    "PolyAmount",
    "QualityCount",
    "RefreshStep",
    "ReportData",
    "ReportSource",
    "ReportWarning",
    "VersionMonth",
    "column_h",
    "describe_close",
    "describe_quality",
    "json_number",
    "month_end",
    "month_label",
    "nest_report",
    "parse_day",
    "parse_month",
    "report_warnings",
]
