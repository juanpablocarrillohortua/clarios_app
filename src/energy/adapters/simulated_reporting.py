"""Synthetic rows shaped like the views of schema ``reporting``, for the chat assistant.

Layer: adapters

Responsibility:
    The plant has not agreed to send its data to the assistant's model vendor,
    so by default the assistant reads this simulation instead
    (``CHAT_DATA_SOURCE=simulated``). It is a small world for fiscal year 2026
    (October 2025 to September 2026) with the twelve views of revision
    ``0006``: **the same names, column names and column order**, which a test
    compares with the real views, so a query written for one runs on the
    other.

    What is real and what is invented:

    * The equation is the repository's own configuration: the drivers, their
      standards, multipliers, weights and processes of version 1 (revision
      ``0003``), the report's sections and rows (``0004``) and the data-quality
      checks. They are restated here because migrations are not imported.
    * Everything else is invented: item codes and descriptions (``SIM-...``),
      production lines, receipts, invoices, quantities, closes and the people
      who closed them. No row comes from the plant.

    The world holds together the way the real views do: ``equs`` is quantity
    times factor; ``equs_mensual`` is ``aporte_cuenta`` summed by month and
    driver, with a zero for each driver nothing fed; ``equs_batt_mensual`` is
    ``equs_mensual`` summed by process; ``equs_poly_mensual`` sums the
    plastic receipts of ``recibos_produccion``; the closed months of
    ``equs_cerrado`` equal ``equs_mensual``, and the one reclose keeps the rows
    it replaced in ``equs_cerrado_historial``.

    It is deterministic: the same rows every time.

Depends on:
    standard library only.
"""

from __future__ import annotations

import calendar
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final

# A fixed seed: the simulation is the same on every machine and in every test.
_SEED: Final = 20261019
_SIX = Decimal("0.000001")

# The DuckDB types of the columns. The real views use PostgreSQL's (numeric,
# text, ...); a test compares names and order, which is what a query needs.
_TEXT: Final = "VARCHAR"
_INT: Final = "INTEGER"
_BIG: Final = "BIGINT"
_NUM: Final = "DECIMAL(24,6)"
_DATE: Final = "DATE"
_BOOL: Final = "BOOLEAN"
_STAMP: Final = "TIMESTAMPTZ"

#: The columns of each view, in the order of revision 0006.
VIEW_COLUMNS: Final[dict[str, tuple[tuple[str, str], ...]]] = {
    "cuentas": (
        ("cuenta_id", _TEXT),
        ("version", _INT),
        ("version_nombre", _TEXT),
        ("vigente_desde", _DATE),
        ("vigente_hasta", _DATE),
        ("cuenta_stats", _TEXT),
        ("descripcion", _TEXT),
        ("proceso", _TEXT),
        ("planta", _TEXT),
        ("area_planta", _TEXT),
        ("orden_proceso", _INT),
        ("fila", _INT),
        ("unidad", _TEXT),
        ("fuente", _TEXT),
        ("estandar_por_bat", _NUM),
        ("multiplicador", _NUM),
        ("peso", _NUM),
        ("factor", _NUM),
        ("suma_eq", _BOOL),
    ),
    "articulos": (
        ("numero_articulo", _TEXT),
        ("descripcion", _TEXT),
        ("descripcion2", _TEXT),
        ("unidad_de_medida", _TEXT),
        ("linea_de_productos", _TEXT),
        ("linea_de_productos_descripcion", _TEXT),
        ("campo_equs", _TEXT),
        ("tipo_de_articulo", _TEXT),
        ("grpo", _TEXT),
        ("familia_placa", _TEXT),
        ("peso_neto", _NUM),
        ("estado", _TEXT),
    ),
    "equs_mensual": (
        ("mes", _DATE),
        ("anio_fiscal", _TEXT),
        ("cuenta_id", _TEXT),
        ("version", _INT),
        ("cuenta_stats", _TEXT),
        ("descripcion", _TEXT),
        ("proceso", _TEXT),
        ("planta", _TEXT),
        ("area_planta", _TEXT),
        ("fila", _INT),
        ("unidad", _TEXT),
        ("cantidad", _NUM),
        ("factor", _NUM),
        ("equs", _NUM),
        ("suma_eq", _BOOL),
    ),
    "equs_batt_mensual": (
        ("mes", _DATE),
        ("anio_fiscal", _TEXT),
        ("equs_orec", _NUM),
        ("equs_ensamble", _NUM),
        ("equs_formacion", _NUM),
        ("equs_decorado", _NUM),
        ("equs_shipping", _NUM),
        ("equs_batt", _NUM),
    ),
    "equs_poly_mensual": (
        ("mes", _DATE),
        ("anio_fiscal", _TEXT),
        ("cuenta", _TEXT),
        ("categoria", _TEXT),
        ("area_planta", _TEXT),
        ("fila", _INT),
        ("piezas", _NUM),
        ("piezas_con_equ", _NUM),
        ("factor_equ", _NUM),
        ("equs", _NUM),
    ),
    "aporte_cuenta": (
        ("origen", _TEXT),
        ("numero_de_transaccion", _BIG),
        ("documento", _TEXT),
        ("fecha_efectiva", _DATE),
        ("mes", _DATE),
        ("anio_fiscal", _TEXT),
        ("numero_articulo", _TEXT),
        ("cuenta_id", _TEXT),
        ("version", _INT),
        ("cuenta_stats", _TEXT),
        ("proceso", _TEXT),
        ("regla_n", _INT),
        ("valor", _NUM),
        ("factor", _NUM),
        ("equs", _NUM),
        ("suma_eq", _BOOL),
    ),
    "recibos_produccion": (
        ("numero_de_transaccion", _BIG),
        ("fecha_efectiva", _DATE),
        ("mes", _DATE),
        ("anio_fiscal", _TEXT),
        ("numero_articulo", _TEXT),
        ("articulo_descripcion", _TEXT),
        ("linea_de_productos", _TEXT),
        ("campo_equs", _TEXT),
        ("almac", _TEXT),
        ("linea_produccion", _TEXT),
        ("area_planta", _TEXT),
        ("cantidad", _NUM),
        ("mat_activo", _NUM),
        ("comp_deco", _NUM),
        ("categoria_poly", _TEXT),
        ("cuenta_poly", _TEXT),
        ("factor_poly", _NUM),
        ("equs_poly", _NUM),
    ),
    "calidad_datos_mensual": (
        ("mes", _DATE),
        ("anio_fiscal", _TEXT),
        ("vista", _TEXT),
        ("descripcion", _TEXT),
        ("bloqueante", _BOOL),
        ("filas", _BIG),
    ),
    "cierres": (
        ("cierre_id", _INT),
        ("mes", _DATE),
        ("anio_fiscal", _TEXT),
        ("cerrado_en", _STAMP),
        ("closed_by", _TEXT),
        ("recierre", _BOOL),
        ("advertencias_aceptadas", _BOOL),
        ("vigente", _BOOL),
    ),
    "cierres_calidad": (
        ("cierre_id", _INT),
        ("mes", _DATE),
        ("vista", _TEXT),
        ("descripcion", _TEXT),
        ("bloqueante", _BOOL),
        ("filas", _BIG),
    ),
    "equs_cerrado": (
        ("mes", _DATE),
        ("anio_fiscal", _TEXT),
        ("cuenta_id", _TEXT),
        ("version", _INT),
        ("cuenta_stats", _TEXT),
        ("descripcion", _TEXT),
        ("proceso", _TEXT),
        ("planta", _TEXT),
        ("area_planta", _TEXT),
        ("fila", _INT),
        ("unidad", _TEXT),
        ("cantidad", _NUM),
        ("factor", _NUM),
        ("equs", _NUM),
        ("suma_eq", _BOOL),
        ("fecha_calculo", _STAMP),
        ("closed_by", _TEXT),
    ),
    "equs_cerrado_historial": (
        ("cierre_id", _INT),
        ("mes", _DATE),
        ("anio_fiscal", _TEXT),
        ("cuenta_id", _TEXT),
        ("version", _INT),
        ("cuenta_stats", _TEXT),
        ("proceso", _TEXT),
        ("cantidad", _NUM),
        ("factor", _NUM),
        ("equs", _NUM),
        ("suma_eq", _BOOL),
        ("fecha_calculo", _STAMP),
        ("closed_by", _TEXT),
    ),
}

# ---------------------------------------------------------------------------
# The equation: the repository's configuration (revisions 0003 and 0004)
# ---------------------------------------------------------------------------
_VERSION = 1
_VERSION_NAME = "STD FY26"
_VERSION_FROM = date(2025, 10, 1)

# Each driver: its code, process, description, unit, source, standard per battery,
# multiplier, weight, and whether it adds to EQUS BATT.
_DRIVERS: Final = (
    ("OXPROD_KG", "OREC", "Produccion de Oxido", "KG", "PRODUCCION", "6.08", "1", "0.074", True),
    (
        "GRIDS_REG_POS",
        "OREC",
        "Produccion Cinta Positiva por factor",
        "KG",
        "PRODUCCION",
        "42",
        "23.634",
        "0.057",
        True,
    ),
    (
        "GRIDS_CAST",
        "OREC",
        "Produccion Rejillas Casting",
        "UN",
        "PRODUCCION",
        "42",
        "1.33",
        "0.057",
        True,
    ),
    (
        "GRIDS_NEG",
        "OREC",
        "Produccion Cinta Negativa por factor",
        "KG",
        "PRODUCCION",
        "42",
        "25.28",
        "0.037",
        True,
    ),
    (
        "PLATEPRD_SLI",
        "OREC",
        "Produccion de Placa sin incluir las de Camion",
        "UN",
        "PRODUCCION",
        "84",
        "1",
        "0.178",
        True,
    ),
    (
        "PLATEPRD_TRUCK",
        "OREC",
        "Produccion de Placa de Camion",
        "UN",
        "PRODUCCION",
        "84",
        "1.14",
        "0.178",
        True,
    ),
    (
        "COSPRODU_SLI",
        "ENSAMBLE",
        "Produccion Baterias Ensambladas sin incluir las de Camion",
        "UN",
        "PRODUCCION",
        "1",
        "1",
        "0.354",
        True,
    ),
    (
        "COSPRODU_TRUCK",
        "ENSAMBLE",
        "Produccion Baterias Ensambladas de Camion",
        "UN",
        "PRODUCCION",
        "1",
        "2.15",
        "0.354",
        True,
    ),
    (
        "POS_ACT_MTL",
        "FORMACION",
        "Pasta Positiva Material Activo de Baterias Formadas",
        "KG",
        "MAT_ACTIVO",
        "3.39",
        "1",
        "0.146",
        True,
    ),
    (
        "15BATPRD",
        "INFO",
        "Produccion de Baterias Formadas",
        "UN",
        "PRODUCCION",
        "1",
        "1",
        "0",
        False,
    ),
    ("FORM_PRD_SLI", "INFO", "Formacion Baterias SLI", "UN", "PRODUCCION", "1", "1", "0", False),
    (
        "FORM_PRD_TRUCK",
        "INFO",
        "Baterias Formadas Heavy Dutty (4D-4DLT-8D).",
        "UN",
        "PRODUCCION",
        "1",
        "1",
        "0",
        False,
    ),
    (
        "COMP_DEC",
        "DECORADO",
        "Cantidad de Componentes de Decoracion",
        "UN",
        "COMP_DECO",
        "4.29",
        "1",
        "0.113",
        True,
    ),
    (
        "CARTON_BATT",
        "DECORADO",
        "Number of batteries placed in Cartons",
        "UN",
        "PRODUCCION",
        "4.29",
        "12.8",
        "0.113",
        True,
    ),
    (
        "UNITSHIP",
        "SHIPPING",
        "Cantidad de Baterias Despachadas",
        "UN",
        "FACTURACION",
        "1",
        "1",
        "0.041",
        True,
    ),
    (
        "LEADPROD",
        "INFO",
        "Produccion de Plomos Refinandos",
        "KG",
        "PRODUCCION",
        "1",
        "1",
        "0",
        False,
    ),
)

# proceso -> (planta, area_planta, orden), from the report's sections.
_SECTIONS: Final = {
    "OREC": ("OREC", "OREC", 1),
    "ENSAMBLE": ("ENSAMBLE", "Ensamble", 2),
    "FORMACION": ("F&A", "F&A", 3),
    "DECORADO": ("F&A", "F&A", 4),
    "SHIPPING": ("DESPACHOS", None, 5),
    "INFO": ("INFORMATIVOS", None, 6),
}
_POLY_AREA: Final = "Componentes"

# The report's row of each driver and plastic account.
_ROWS: Final = {
    "OXPROD_KG": 2,
    "GRIDS_REG_POS": 3,
    "GRIDS_CAST": 4,
    "GRIDS_NEG": 5,
    "PLATEPRD_SLI": 6,
    "PLATEPRD_TRUCK": 7,
    "COSPRODU_SLI": 8,
    "COSPRODU_TRUCK": 9,
    "POS_ACT_MTL": 10,
    "15BATPRD": 11,
    "FORM_PRD_SLI": 12,
    "FORM_PRD_TRUCK": 13,
    "COMP_DEC": 14,
    "CARTON_BATT": 15,
    "UNITSHIP": 16,
    "LEADPROD": 18,
    "CONTPROD_CAR": 20,
    "COVRPROD_SLI1": 23,
    "TOPLIDPROD": 27,
    "VENTPROD": 28,
    "Small_Comp_Prod": 29,
}

# (vista, descripcion, bloqueante), the data-quality checks.
_CHECKS: Final = (
    (
        "dq_sin_regla",
        "Production rows of product lines 2101 to 3104 that match no rule of the version in force",
        True,
    ),
    ("dq_doble_regla", "Production rows that match two or more rules of the same process", True),
    (
        "dq_sin_conversor",
        "Formed batteries produced without paste, or decorated batteries produced "
        "without decoration pieces",
        True,
    ),
    (
        "dq_categoria_poly",
        "Production rows whose Campo Equs differs from the Clasif_Poly category of the item",
        False,
    ),
    (
        "dq_linea_sin_planta",
        "Production receipts on production lines that have no plant area",
        False,
    ),
    (
        "dq_variacion",
        "Drivers whose monthly quantity moved more than the threshold against the previous month",
        False,
    ),
)

# ---------------------------------------------------------------------------
# The invented part
# ---------------------------------------------------------------------------
_MONTHS: Final = (
    *(date(2025, month, 1) for month in (10, 11, 12)),
    *(date(2026, month, 1) for month in range(1, 10)),
)
_FISCAL_YEAR: Final = "FY26"
_CLOSED_MONTHS: Final = _MONTHS[:-1]  # October to August; September is open
_RECLOSED_MONTH: Final = date(2026, 1, 1)
_CLOSED_BY: Final = "sim-closer"

# A month's activity against an average month: a December and a March with
# holidays, a strong October, as plants have.
_SEASON: Final = (1.15, 0.95, 0.75, 1.0, 1.0, 0.6, 0.8, 0.95, 1.05, 1.05, 0.8, 1.0)

# Simulated items: (code, description, product line, its description, campo_equs, unit)
_ITEMS: Final = (
    ("SIM-2101-01", "SIM lead oxide", "2101", "Oxido De Plomo", "OXIDO", "KG"),
    ("SIM-2103-01", "SIM positive strip", "2103", "Cinta De Plomo", "CINTA(+)", "KG"),
    ("SIM-2103-02", "SIM negative strip", "2103", "Cinta De Plomo", "CINTA(-)", "KG"),
    ("SIM-2102-01", "SIM cast grid", "2102", "Rejillas", "REJILLA", "UN"),
    ("SIM-2104-01", "SIM car plate", "2104", "Placas Cominco", "PLACA SLI", "UN"),
    ("SIM-2105-01", "SIM truck plate", "2105", "Placas Casting JCI", "PLACA TRUCK", "UN"),
    ("SIM-2201-01", "SIM assembled car battery", "2201", "Baterias Ensambladas", "SLI", "UN"),
    ("SIM-2201-02", "SIM assembled truck battery", "2201", "Baterias Ensambladas", "TRUCK-B", "UN"),
    ("SIM-3101-01", "SIM formed car battery", "3101", "Baterias Formadas", "SLI", "UN"),
    ("SIM-3101-02", "SIM formed truck battery", "3101", "Baterias Formadas", "TRUCK-B", "UN"),
    ("SIM-3103-01", "SIM decorated car battery", "3103", "Baterias Decoradas", "SLI", "UN"),
    ("SIM-3103-02", "SIM decorated truck battery", "3103", "Baterias Decoradas", "TRUCK-B", "UN"),
    ("SIM-1104-01", "SIM refined lead", "1104", "Plomos Puros", "N/A", "KG"),
    ("SIM-1201-01", "SIM car container", "1201", "Cajas", "CAJA SLI", "UN"),
    ("SIM-1200-01", "SIM car cover", "1200", "Cubiertas", "TAPA SLI", "UN"),
    ("SIM-1200-02", "SIM top lid", "1200", "Cubiertas", "SOBRECUB", "UN"),
    ("SIM-1204-01", "SIM vent plug", "1204", "Tapones Intercompany", "TAPONES", "UN"),
    ("SIM-1202-01", "SIM small accessory", "1202", "Manijas - Acces Interco", "ACCESOR", "UN"),
)

# driver -> (item code, monthly EQ of an average month, unit of a receipt's quantity).
# Formation and decoration are fed by battery receipts (see _battery_receipts).
_SIMPLE: Final = {
    "OXPROD_KG": ("SIM-2101-01", 18_000),
    "GRIDS_REG_POS": ("SIM-2103-01", 3_600),
    "GRIDS_NEG": ("SIM-2103-02", 1_500),
    "GRIDS_CAST": ("SIM-2102-01", 15_000),
    "PLATEPRD_SLI": ("SIM-2104-01", 30_000),
    "PLATEPRD_TRUCK": ("SIM-2105-01", 2_500),
    "COSPRODU_SLI": ("SIM-2201-01", 55_000),
    "COSPRODU_TRUCK": ("SIM-2201-02", 20_000),
    "LEADPROD": ("SIM-1104-01", 0),
}
_LEAD_KG_A_MONTH: Final = 1_500_000
_FORMED_A_MONTH: Final = 170_000  # batteries, of which about 7 % truck
_PASTE_KG_PER_BATTERY: Final = Decimal("3.4")
_DECO_PIECES_PER_BATTERY: Final = Decimal("4.3")
_SHIPPED_A_MONTH: Final = 190_000

# account -> (item code, category, factor_equ, pieces in an average month)
_POLY: Final = {
    "CONTPROD_CAR": ("SIM-1201-01", "CAJA SLI", Decimal("1.0"), 9_000),
    "COVRPROD_SLI1": ("SIM-1200-01", "TAPA SLI", Decimal("0.6"), 8_000),
    "TOPLIDPROD": ("SIM-1200-02", "SOBRECUB", Decimal("0.25"), 6_000),
    "VENTPROD": ("SIM-1204-01", "TAPONES", Decimal("0.05"), 40_000),
    "Small_Comp_Prod": ("SIM-1202-01", "ACCESOR", Decimal("0.02"), 30_000),
}

_RECEIPTS_PER_DRIVER: Final = 8


@dataclass(frozen=True, slots=True)
class _Driver:
    """One driver of version 1, as the simulation uses it."""

    proceso: str
    descripcion: str
    unidad: str
    fuente: str
    estandar: Decimal
    multiplicador: Decimal
    peso: Decimal
    factor: Decimal
    suma_eq: bool


@dataclass(frozen=True, slots=True)
class SimulatedView:
    """One simulated view: its columns, with their types, and its rows."""

    columns: tuple[tuple[str, str], ...]
    rows: tuple[tuple[Any, ...], ...]


def _q(value: Decimal | float | int) -> Decimal:
    """Round a number to the six places of the simulated numeric columns."""
    return Decimal(str(value)).quantize(_SIX, rounding=ROUND_HALF_UP)


def _factor(estandar: str, multiplicador: str, peso: str) -> Decimal:
    """Return a driver's factor, as revision 0003 computes it."""
    return _q(Decimal(multiplicador) / Decimal(estandar) * Decimal(peso))


def _month_end(month: date) -> date:
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def _weekday_in(month: date, rng: random.Random) -> date:
    """Return a working day (Monday to Saturday) of the month."""
    end = _month_end(month)
    while True:
        day = month + timedelta(days=rng.randrange((end - month).days + 1))
        if day.weekday() < 6:  # noqa: PLR2004 (Sunday is 6)
            return day


def _split(total: Decimal, parts: int, rng: random.Random, places: Decimal) -> list[Decimal]:
    """Split a quantity into parts of random sizes that add up to it exactly."""
    weights = [rng.uniform(0.5, 1.5) for _ in range(parts)]
    scale = sum(weights)
    pieces = [(total * Decimal(str(w / scale))).quantize(places) for w in weights[:-1]]
    return [*pieces, total - sum(pieces, Decimal(0))]


class _World:
    """Builds the simulated views once, in an order where each reads what it needs."""

    def __init__(self) -> None:
        self.rng = random.Random(_SEED)  # noqa: S311  # nosec B311
        self.drivers = {
            d[0]: _Driver(
                proceso=d[1],
                descripcion=d[2],
                unidad=d[3],
                fuente=d[4],
                estandar=Decimal(d[5]),
                multiplicador=Decimal(d[6]),
                peso=Decimal(d[7]),
                factor=_factor(d[5], d[6], d[7]),
                suma_eq=d[8],
            )
            for d in _DRIVERS
        }
        self.items = {item[0]: item for item in _ITEMS}
        self.transaction = 90_000_000
        self.invoice = 700_000
        # Each contribution: its origin, transaction, document, day, item, driver and value.
        self.contributions: list[tuple[str, int | None, str, date, str, str, Decimal]] = []
        # Each receipt is a row of recibos_produccion.
        self.receipts: list[tuple[Any, ...]] = []

    # -- receipts ------------------------------------------------------------
    def _receipt(
        self,
        day: date,
        item: str,
        quantity: Decimal,
        line: str,
        *,
        paste: Decimal | None = None,
        deco: Decimal | None = None,
        poly: tuple[str, str, Decimal] | None = None,
    ) -> int:
        self.transaction += self.rng.randrange(1, 40)
        code, description, product_line, _, campo, _ = self.items[item]
        area = _line_area(line)
        month = day.replace(day=1)
        category, account, factor = (None, None, None) if poly is None else poly
        self.receipts.append(
            (
                self.transaction,
                day,
                month,
                _FISCAL_YEAR,
                code,
                description,
                product_line,
                campo,
                "1010" if poly else "1515",
                line,
                area,
                quantity,
                paste,
                deco,
                category,
                account,
                factor,
                None if poly is None else _q(quantity * factor) if factor else None,
            )
        )
        return self.transaction

    def _contribute(
        self, transaction: int, day: date, item: str, driver: str, value: Decimal
    ) -> None:
        self.contributions.append(
            ("recibo", transaction, str(transaction), day, item, driver, value)
        )

    def build_movements(self) -> None:
        """Invent every receipt and invoice of the year."""
        for index, month in enumerate(_MONTHS):
            season = _SEASON[index]
            self._simple_receipts(month, season)
            self._battery_receipts(month, season)
            self._invoices(month, season)
            self._poly_receipts(month, season)

    def _simple_receipts(self, month: date, season: float) -> None:
        for driver, (item, monthly_eq) in _SIMPLE.items():
            info = self.drivers[driver]
            if driver == "LEADPROD":
                total = _LEAD_KG_A_MONTH * season * self.rng.uniform(0.85, 1.15)
            else:
                total = monthly_eq * season * self.rng.uniform(0.9, 1.1) / float(info.factor)
            places = Decimal(1) if info.unidad == "UN" else Decimal("0.01")
            amount = Decimal(str(total)).quantize(places)
            for value in _split(amount, _RECEIPTS_PER_DRIVER, self.rng, places):
                day = _weekday_in(month, self.rng)
                line = _PRODUCTION_LINES[info.proceso] if driver != "LEADPROD" else "SIM-RC-01"
                transaction = self._receipt(day, item, value, line)
                self._contribute(transaction, day, item, driver, value)

    def _battery_receipts(self, month: date, season: float) -> None:
        """Formed batteries feed paste and the formed counts; decorated ones, the pieces."""
        formed = Decimal(str(_FORMED_A_MONTH * season * self.rng.uniform(0.9, 1.1))).quantize(
            Decimal(1)
        )
        for value in _split(formed, _RECEIPTS_PER_DRIVER * 2, self.rng, Decimal(1)):
            truck = self.rng.random() < 0.07  # noqa: PLR2004 (the share of truck batteries)
            day = _weekday_in(month, self.rng)
            formed_item = "SIM-3101-02" if truck else "SIM-3101-01"
            paste = _q(value * _PASTE_KG_PER_BATTERY * (Decimal("1.6") if truck else Decimal(1)))
            transaction = self._receipt(day, formed_item, value, "SIM-FOR-01", paste=paste)
            self._contribute(transaction, day, formed_item, "POS_ACT_MTL", paste)
            self._contribute(
                transaction, day, formed_item, "FORM_PRD_TRUCK" if truck else "FORM_PRD_SLI", value
            )
            # The same batteries are decorated, mostly the same day.
            deco_day = min(day + timedelta(days=self.rng.randrange(2)), _month_end(month))
            deco_item = "SIM-3103-02" if truck else "SIM-3103-01"
            pieces = _q(value * _DECO_PIECES_PER_BATTERY)
            transaction = self._receipt(deco_day, deco_item, value, "SIM-DEC-01", deco=pieces)
            self._contribute(transaction, deco_day, deco_item, "COMP_DEC", pieces)

    def _invoices(self, month: date, season: float) -> None:
        shipped = Decimal(str(_SHIPPED_A_MONTH * season * self.rng.uniform(0.9, 1.1))).quantize(
            Decimal(1)
        )
        for value in _split(shipped, _RECEIPTS_PER_DRIVER * 3, self.rng, Decimal(1)):
            self.invoice += self.rng.randrange(1, 30)
            day = _weekday_in(month, self.rng)
            item = self.rng.choice(("SIM-3103-01", "SIM-3103-02"))
            document = f"SIM-F{self.invoice}/1/1"
            self.contributions.append(("factura", None, document, day, item, "UNITSHIP", value))

    def _poly_receipts(self, month: date, season: float) -> None:
        for account, (item, category, factor, pieces) in _POLY.items():
            total = Decimal(str(pieces * season * self.rng.uniform(0.85, 1.15))).quantize(
                Decimal(1)
            )
            for value in _split(total, 4, self.rng, Decimal(1)):
                day = _weekday_in(month, self.rng)
                self._receipt(day, item, value, "SIM-INY-01", poly=(category, account, factor))

    # -- views ---------------------------------------------------------------
    def cuentas(self) -> list[tuple[Any, ...]]:
        rows = []
        for code, info in self.drivers.items():
            planta, area, order = _SECTIONS[info.proceso]
            rows.append(
                (
                    f"{_VERSION}:{code}",
                    _VERSION,
                    _VERSION_NAME,
                    _VERSION_FROM,
                    None,
                    code,
                    info.descripcion,
                    info.proceso,
                    planta,
                    area,
                    order,
                    _ROWS[code],
                    info.unidad,
                    info.fuente,
                    _q(info.estandar),
                    _q(info.multiplicador),
                    _q(info.peso),
                    info.factor,
                    info.suma_eq,
                )
            )
        return rows

    def articulos(self) -> list[tuple[Any, ...]]:
        return [
            (
                code,
                description,
                None,
                unit,
                line,
                line_description,
                campo,
                "SIM",
                None,
                None,
                _q(1),
                "AC",
            )
            for code, description, line, line_description, campo, unit in _ITEMS
        ]

    def aporte_cuenta(self) -> list[tuple[Any, ...]]:
        rows = []
        for origen, transaction, document, day, item, driver, value in self.contributions:
            info = self.drivers[driver]
            rows.append(
                (
                    origen,
                    transaction,
                    document,
                    day,
                    day.replace(day=1),
                    _FISCAL_YEAR,
                    item,
                    f"{_VERSION}:{driver}",
                    _VERSION,
                    driver,
                    info.proceso,
                    1,
                    value,
                    info.factor,
                    _q(value * info.factor),
                    info.suma_eq,
                )
            )
        return rows

    def monthly(
        self, aporte: list[tuple[Any, ...]]
    ) -> dict[tuple[date, str], tuple[Decimal, Decimal]]:
        """Return (month, driver) -> (quantity, EQ), summed from the contributions."""
        totals: dict[tuple[date, str], tuple[Decimal, Decimal]] = {}
        for row in aporte:
            key = (row[4], row[9])
            quantity, equs = totals.get(key, (Decimal(0), Decimal(0)))
            totals[key] = (quantity + row[12], equs + row[14])
        return totals

    def equs_mensual(
        self, totals: dict[tuple[date, str], tuple[Decimal, Decimal]]
    ) -> list[tuple[Any, ...]]:
        rows = []
        for month in _MONTHS:
            for code, info in self.drivers.items():
                planta, area, _ = _SECTIONS[info.proceso]
                quantity, equs = totals.get((month, code), (Decimal(0), Decimal(0)))
                rows.append(
                    (
                        month,
                        _FISCAL_YEAR,
                        f"{_VERSION}:{code}",
                        _VERSION,
                        code,
                        info.descripcion,
                        info.proceso,
                        planta,
                        area,
                        _ROWS[code],
                        info.unidad,
                        _q(quantity),
                        info.factor,
                        _q(equs),
                        info.suma_eq,
                    )
                )
        return rows


_PRODUCTION_LINES: Final = {"OREC": "SIM-ORE-01", "ENSAMBLE": "SIM-ENS-01"}


def _line_area(line: str) -> str | None:
    """Return the plant area of a simulated production line."""
    return {
        "SIM-ORE-01": "OREC",
        "SIM-ENS-01": "Ensamble",
        "SIM-FOR-01": "F&A",
        "SIM-DEC-01": "F&A",
        "SIM-INY-01": _POLY_AREA,
    }.get(line)


def _batt_mensual(equs_mensual: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    processes = ("OREC", "ENSAMBLE", "FORMACION", "DECORADO", "SHIPPING")
    rows = []
    for month in _MONTHS:
        by_process = dict.fromkeys(processes, Decimal(0))
        for row in equs_mensual:
            if row[0] == month and row[14] and row[6] in by_process:
                by_process[row[6]] += row[13]
        values = [_q(by_process[p]) for p in processes]
        rows.append((month, _FISCAL_YEAR, *values, _q(sum(values, Decimal(0)))))
    return rows


def _poly_mensual(receipts: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    totals: dict[tuple[date, str, str], tuple[Decimal, Decimal, Decimal]] = {}
    for row in receipts:
        if row[15] is None:
            continue
        key = (row[2], row[15], row[14])
        pieces, factor, equs = totals.get(key, (Decimal(0), row[16], Decimal(0)))
        totals[key] = (pieces + row[11], factor, equs + (row[17] or Decimal(0)))
    return [
        (
            month,
            _FISCAL_YEAR,
            account,
            category,
            _POLY_AREA,
            _ROWS[account],
            _q(pieces),
            _q(pieces) if factor and factor > 0 else _q(0),
            factor,
            _q(equs),
        )
        for (month, account, category), (pieces, factor, equs) in sorted(totals.items())
    ]


def _quality(rng: random.Random) -> dict[date, dict[str, int]]:
    """Invent how many rows each check finds each month; few, often none."""
    counts: dict[date, dict[str, int]] = {}
    for month in _MONTHS:
        counts[month] = {
            view: (rng.choice((0, 0, 0, 1, 2, 3, 5, 8)) if blocking else rng.randrange(0, 40))
            for view, _, blocking in _CHECKS
        }
    return counts


def _closes(
    quality: dict[date, dict[str, int]], equs_mensual: list[tuple[Any, ...]]
) -> tuple[list[tuple[Any, ...]], ...]:
    """Close October to August; reclose January once, keeping what it replaced."""
    cierres: list[tuple[Any, ...]] = []
    cierres_calidad: list[tuple[Any, ...]] = []
    cerrado: list[tuple[Any, ...]] = []
    historial: list[tuple[Any, ...]] = []
    closed_at: dict[int, datetime] = {}
    plan = [(month, False) for month in _CLOSED_MONTHS]
    plan.insert(_CLOSED_MONTHS.index(_RECLOSED_MONTH) + 2, (_RECLOSED_MONTH, True))
    latest: dict[date, int] = {}
    for cierre_id, (month, reclose) in enumerate(plan, start=1):
        when = datetime.combine(_month_end(month) + timedelta(days=4), datetime.min.time(), UTC)
        when += timedelta(hours=15, days=30 if reclose else 0)
        closed_at[cierre_id] = when
        warnings = any(quality[month][view] for view, _, blocking in _CHECKS if blocking)
        cierres.append((cierre_id, month, _FISCAL_YEAR, when, _CLOSED_BY, reclose, warnings))
        for view, description, blocking in _CHECKS:
            cierres_calidad.append(
                (cierre_id, month, view, description, blocking, quality[month][view])
            )
        if reclose:
            first = latest[month]
            for row in equs_mensual:
                if row[0] == month:
                    # The first close counted 2 % less of each driver than the reclose.
                    quantity = _q(row[11] * Decimal("0.98"))
                    historial.append(
                        (
                            cierre_id,
                            month,
                            _FISCAL_YEAR,
                            row[2],
                            row[3],
                            row[4],
                            row[6],
                            quantity,
                            row[12],
                            _q(quantity * row[12]),
                            row[14],
                            closed_at[first],
                            _CLOSED_BY,
                        )
                    )
        latest[month] = cierre_id
    vigentes = set(latest.values())
    cierres = [(*row, row[0] in vigentes) for row in cierres]
    for row in equs_mensual:
        if row[0] in latest:
            cerrado.append((*row, closed_at[latest[row[0]]], _CLOSED_BY))
    return cierres, cierres_calidad, cerrado, historial


def simulated_views() -> dict[str, SimulatedView]:
    """Build the twelve simulated views.

    Returns:
        Each view's name, without the schema, with its columns and rows.
    """
    world = _World()
    world.build_movements()
    aporte = world.aporte_cuenta()
    equs_mensual = world.equs_mensual(world.monthly(aporte))
    quality = _quality(world.rng)
    cierres, cierres_calidad, cerrado, historial = _closes(quality, equs_mensual)
    calidad = [
        (month, _FISCAL_YEAR, view, description, blocking, quality[month][view])
        for month in _MONTHS
        for view, description, blocking in _CHECKS
    ]
    rows: dict[str, list[tuple[Any, ...]]] = {
        "cuentas": world.cuentas(),
        "articulos": world.articulos(),
        "equs_mensual": equs_mensual,
        "equs_batt_mensual": _batt_mensual(equs_mensual),
        "equs_poly_mensual": _poly_mensual(world.receipts),
        "aporte_cuenta": aporte,
        "recibos_produccion": world.receipts,
        "calidad_datos_mensual": calidad,
        "cierres": cierres,
        "cierres_calidad": cierres_calidad,
        "equs_cerrado": cerrado,
        "equs_cerrado_historial": historial,
    }
    return {
        name: SimulatedView(columns=VIEW_COLUMNS[name], rows=tuple(rows[name]))
        for name in VIEW_COLUMNS
    }


__all__ = ["VIEW_COLUMNS", "SimulatedView", "simulated_views"]
