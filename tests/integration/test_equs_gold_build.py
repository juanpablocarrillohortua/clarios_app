"""EQUS Gold on the real workbooks: the acceptance tests of the Gold layer.

Layer: tests (integration)

The six workbooks of ``data/raw_excels`` are landed in a database at head,
Silver is built and Gold refreshed, once for the module. Then Gold is checked
against three things:

* the figures ``docs/EQUS_CONTEXT.md`` section 9 documents for these files, to
  the cent;
* column H of sheet RESUMEN EQU of the EQUS BATT workbook itself, row by row,
  read from the workbook's cached values (never copied here by hand), so the
  test still means something when next month's workbook arrives. Only the
  differences section 9 explains may differ, and each is asserted at its exact
  value;
* the date-range report and the column-H command, which must say the same.

The workbooks are not in the repository, so the module is skipped when they are
absent. Two tests also need ``make``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jsonschema
import pytest
from sqlalchemy import text

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.adapters.local_storage import LocalObjectStorage
from energy.adapters.postgres_bronze_repo import PostgresBronzeLandingRepository
from energy.domain.exceptions import CloseRefusedError, InvalidPeriodError
from energy.pipelines.flows.equs_bronze_load import ROUTES, find_workbook, load_all
from energy.pipelines.flows.equs_gold import close_month, refresh_gold
from energy.pipelines.flows.equs_report import build_report, load_schema
from energy.services.bronze_loader import BronzeLoader

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection, Engine

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "raw_excels"

_MISSING = [
    route.filename
    for route in ROUTES
    if not route.optional and find_workbook(DATA_DIR, route.filename) is None
]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(bool(_MISSING), reason=f"source workbooks not available: {_MISSING}"),
]

# The documented figures belong to six named workbooks, so exports of other
# periods that may sit beside them are left out.
EXACT_ROUTES = tuple(replace(route, many=False) for route in ROUTES)

CENT = Decimal("0.01")
SEPTEMBER = date(2026, 9, 1)
AUGUST = date(2026, 8, 1)
MAKE = shutil.which("make")

# EQUS_CONTEXT section 9.1, target column.
SEPTEMBER_EQ = {
    "OREC": Decimal("71040.72"),
    "ENSAMBLE": Decimal("85014.78"),
    "FORMACION": Decimal("27940.86"),
    "DECORADO": Decimal("23615.00"),
    "SHIPPING": Decimal("7978.97"),
    "EQUS BATT": Decimal("215590.33"),
    "EQUS POLY": Decimal("503033.19"),
}
SEPTEMBER_QUANTITIES = {
    "OXPROD_KG": Decimal(1162129),
    "GRIDS_CAST": Decimal(9761100),
    "GRIDS_NEG": Decimal(264006),
    "PLATEPRD_SLI": Decimal(14859820),
    "PLATEPRD_TRUCK": Decimal(790407),
    "COSPRODU_SLI": Decimal(205185),
    "COSPRODU_TRUCK": Decimal(16265),
    "POS_ACT_MTL": Decimal("648763.763"),
    "COMP_DEC": Decimal(896534),
    "UNITSHIP": Decimal(194609),
}

# EQUS_CONTEXT section 9.2: (OREC, assembly, formation, decoration, shipping, EQUS BATT).
EQ_BY_MONTH = {
    "2025-10": ("96087.03", "113560.03", "25850.60", "22594.51", "11683.24", "269775.41"),
    "2025-11": ("68626.84", "74008.00", "26810.80", "22471.41", "7625.02", "199542.07"),
    "2025-12": ("47843.26", "61534.55", "18167.56", "16960.54", "7867.70", "152373.59"),
    "2026-01": ("73321.33", "80682.71", "26004.14", "22657.61", "7780.16", "210445.95"),
    "2026-02": ("71903.12", "78049.65", "25793.29", "23659.73", "7986.92", "207392.72"),
    "2026-03": ("31579.99", "36719.71", "17602.28", "15550.75", "5969.93", "107422.66"),
    "2026-04": ("52322.01", "55410.03", "20265.43", "18345.91", "6303.59", "152646.95"),
    "2026-05": ("64957.93", "74283.01", "25009.54", "19518.58", "8414.35", "192183.40"),
    "2026-06": ("75433.48", "81922.26", "31470.00", "24206.55", "9138.37", "222170.65"),
    "2026-07": ("79039.71", "81625.89", "28667.21", "23561.24", "9015.29", "221909.32"),
    "2026-08": ("58159.65", "52244.01", "22934.64", "19058.99", "6873.24", "159270.54"),
    "2026-09": ("71040.72", "85014.78", "27940.86", "23615.00", "7978.97", "215590.33"),
}

# The rows of RESUMEN EQU that may differ, and by how much exactly. Each comes
# from a defect of the workbook section 9 explains; a change in any of them
# means the workbook or a rule changed, and someone must look.
#   POS_ACT_MTL: plate 215000015 is listed twice in sheet PLACAS, which doubles
#       the paste of the batteries that use it.
#   COMP_DEC: the workbook's formula stops counting the TERM-PRT protectors of
#       imported batteries from row 29,261 of ESTRUCTURA DECORADO.
#   H36, H40, H44, H45: the totals that carry those two rows.
KNOWN = {
    "POS_ACT_MTL": {
        "excel_d": Decimal("654835.08"),
        "db_d": Decimal("648763.763"),
        "excel_h": Decimal("28202.34"),
        "db_h": Decimal("27940.86"),
    },
    "COMP_DEC": {
        "excel_d": Decimal(895564),
        "db_d": Decimal(896534),
        "excel_h": Decimal("23589.45"),
        "db_h": Decimal("23615.00"),
    },
    "H36 EQUS BATT": {"excel_h": Decimal("215826.26"), "db_h": Decimal("215590.33")},
    "H40 EQUS F&A": {"excel_h": Decimal("51791.78"), "db_h": Decimal("51555.86")},
    "H44 EQUS FORMACION": {"excel_h": Decimal("28202.34"), "db_h": Decimal("27940.86")},
    "H45 EQUS DECORADO": {"excel_h": Decimal("23589.45"), "db_h": Decimal("23615.00")},
}
# Drivers the database has no row for in the month: the workbook must say 0.
ABSENT_IN_DB = ("GRIDS_REG_POS", "CARTON_BATT")
# Informative drivers: the workbook gives no column H.
QUANTITY_ONLY = ("FORM_PRD_SLI", "FORM_PRD_TRUCK", "LEADPROD")
TOTALS = {
    32: "H32 EQUS POLY",
    36: "H36 EQUS BATT",
    38: "H38 EQUS OREC",
    39: "H39 EQUS ENSAMBLE",
    40: "H40 EQUS F&A",
    41: "H41 EQUS SHIPPING",
    44: "H44 EQUS FORMACION",
    45: "H45 EQUS DECORADO",
}


def _cents(value: Decimal) -> Decimal:
    """Round to the cent as the specification's figures were rounded: half up.

    July 2026 has a half cent: shipping is 219,885 x 0.041 = 9,015.285 exactly.
    """
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return Decimal(repr(float(value)))


def _cells(filename: str, sheet: str) -> list[list[Any]]:
    path = find_workbook(DATA_DIR, filename)
    assert path is not None
    return [list(row) for row in CalamineWorkbookReader().open_workbook(path.read_bytes())
            .sheet(sheet).rows]  # fmt: skip


# =============================================================================
# The workbook's RESUMEN EQU
# =============================================================================
@dataclass(frozen=True)
class Workbook:
    """Columns D, E and H of RESUMEN EQU, and the month its REPORTE QAD holds."""

    month: date
    drivers: dict[str, tuple[Decimal | None, Decimal | None, Decimal | None]]
    poly: dict[str, tuple[Decimal | None, Decimal | None]]
    totals: dict[str, Decimal]


@pytest.fixture(scope="module")
def workbook() -> Workbook:
    """Read EQUS BATT.xlsx: cached values, not formulas."""
    report = _cells("EQUS BATT.xlsx", "REPORTE QAD")
    column = report[0].index("Fecha Efectiva")
    months = {
        (row[column].year, row[column].month)
        for row in report[1:]
        if isinstance(row[column], date | datetime)
    }
    assert len(months) == 1, months
    (year, number) = months.pop()

    rows = _cells("EQUS BATT.xlsx", "RESUMEN EQU")

    def cell(row: int, column: int) -> Any:
        values = rows[row - 1]
        return values[column] if len(values) > column else None

    drivers = {
        str(cell(row, 0)): (_decimal(cell(row, 3)), _decimal(cell(row, 4)), _decimal(cell(row, 7)))
        for row in range(2, 19)
        if cell(row, 0)
    }
    poly = {
        str(cell(row, 0)): (_decimal(cell(row, 3)), _decimal(cell(row, 7)))
        for row in range(20, 31)
        if cell(row, 0)
    }
    totals = {label: _decimal(cell(row, 7)) for row, label in TOTALS.items()}
    assert all(value is not None for value in totals.values())
    return Workbook(date(year, number, 1), drivers, poly, totals)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Side:
    """What the database says for the rows the reconciliation compares."""

    drivers: dict[str, tuple[Decimal, Decimal | None]]
    poly: dict[str, Decimal]
    totals: dict[str, Decimal]


@dataclass(frozen=True)
class Line:
    """One row of the reconciliation table."""

    key: str
    excel_d: Decimal | None
    db_d: Decimal | None
    excel_h: Decimal | None
    db_h: Decimal | None
    status: str
    note: str = ""

    @property
    def difference(self) -> Decimal | None:
        if self.excel_h is None or self.db_h is None:
            return None
        return self.db_h - self.excel_h


def _close(a: Decimal | None, b: Decimal | None, tolerance: Decimal) -> bool:
    return a is not None and b is not None and abs(a - b) <= tolerance


def _driver_line(cuenta: str, book: Workbook, side: Side) -> Line:
    """Compare one driver row (rows 2 to 18) with the database."""
    exact = Decimal("0.0005")
    excel_d, excel_e, excel_h = book.drivers[cuenta]
    db = side.drivers.get(cuenta)
    db_d, db_h = (db[0], db[1]) if db else (None, None)

    if cuenta in KNOWN:
        known = KNOWN[cuenta]
        ok = (
            _close(excel_d, known["excel_d"], CENT)
            and _close(db_d, known["db_d"], exact)
            and _close(excel_h, known["excel_h"], CENT)
            and _close(db_h, known["db_h"], CENT)
        )
        note = "known difference" if ok else "the known difference changed"
        return Line(cuenta, excel_d, db_d, excel_h, db_h, "KNOWN" if ok else "FAIL", note)
    if cuenta in ABSENT_IN_DB:
        ok = (excel_d or 0) == 0 and (excel_h or 0) == 0 and (db_d or 0) == 0
        note = "no production; the database has no row"
        return Line(cuenta, excel_d, db_d, excel_h, db_h, "OK" if ok else "FAIL", note)
    if cuenta == "15BATPRD":
        formed = sum(
            (side.drivers.get(c, (Decimal(0), None))[0] for c in QUANTITY_ONLY[:2]),
            start=Decimal(0),
        )
        note = "no rule: FORM_PRD_SLI + FORM_PRD_TRUCK"
        return Line(
            cuenta, excel_d, formed, None, None, "OK" if excel_d == formed else "FAIL", note
        )
    if cuenta in QUANTITY_ONLY:
        ok = _close(excel_d, db_d, exact)
        note = "quantity only"
        if cuenta == "LEADPROD":
            ok = ok and excel_e is not None and db_d is not None and excel_e * 1000 == db_d
            note = "quantity only; the workbook shows tonnes in column E"
        return Line(cuenta, excel_d, db_d, None, None, "OK" if ok else "FAIL", note)
    ok = _close(excel_d, db_d, exact) and _close(excel_h, db_h, CENT)
    return Line(cuenta, excel_d, db_d, excel_h, db_h, "OK" if ok else "FAIL")


def _poly_lines(book: Workbook, side: Side) -> list[Line]:
    """Compare the EQUS POLY rows (20 to 30) with the database."""
    lines = [
        Line(
            f"POLY {cuenta}",
            excel_d,
            None,
            excel_h,
            side.poly.get(cuenta),
            "OK" if _close(excel_h, side.poly.get(cuenta), CENT) else "FAIL",
        )
        for cuenta, (excel_d, excel_h) in book.poly.items()
    ]
    for cuenta in sorted(set(side.poly) - set(book.poly)):
        # The pellet has no row in RESUMEN EQU; accepted while it carries no EQ.
        ok = cuenta == "NA" and side.poly[cuenta] == 0
        note = "only in the database (PELLET, EQ 0)"
        lines.append(
            Line(
                f"POLY {cuenta}", None, None, None, side.poly[cuenta], "OK" if ok else "FAIL", note
            )
        )
    return lines


def _total_line(label: str, excel_h: Decimal, side: Side) -> Line:
    """Compare one total of rows 32 to 45 with the database."""
    db_h = side.totals.get(label)
    if label in KNOWN:
        known = KNOWN[label]
        ok = _close(excel_h, known["excel_h"], CENT) and _close(db_h, known["db_h"], CENT)
        note = "carries the known differences"
        return Line(label, None, None, excel_h, db_h, "KNOWN" if ok else "FAIL", note)
    return Line(label, None, None, excel_h, db_h, "OK" if _close(excel_h, db_h, CENT) else "FAIL")


def reconcile(book: Workbook, side: Side) -> list[Line]:
    """Compare column D and H of RESUMEN EQU with the database, row by row."""
    lines = [_driver_line(cuenta, book, side) for cuenta in book.drivers]
    lines += [
        Line(cuenta, None, side.drivers[cuenta][0], None, side.drivers[cuenta][1], "FAIL",
             "only in the database")
        for cuenta in sorted(set(side.drivers) - set(book.drivers))
    ]  # fmt: skip
    lines += _poly_lines(book, side)
    lines += [_total_line(label, excel_h, side) for label, excel_h in book.totals.items()]
    return lines


def _table(lines: list[Line]) -> str:
    def number(value: Decimal | None, places: int) -> str:
        return "" if value is None else f"{value:,.{places}f}"

    header = (
        f"{'cuenta STATS':<24} {'Excel D':>16} {'DB cantidad':>16} {'Excel H':>13} "
        f"{'DB equs':>13} {'difference':>11}  status"
    )
    rows = [
        f"{line.key:<24} {number(line.excel_d, 3):>16} {number(line.db_d, 3):>16} "
        f"{number(line.excel_h, 2):>13} {number(line.db_h, 2):>13} "
        f"{number(line.difference, 2):>11}  {line.status}  {line.note}"
        for line in lines
    ]
    return "\n".join([header, *rows])


def _assert_reconciled(lines: list[Line]) -> None:
    print("\n" + _table(lines))  # noqa: T201 - the table is part of the test's output
    failed = [line for line in lines if line.status == "FAIL"]
    assert not failed, "rows that differ from RESUMEN EQU:\n" + _table(failed)
    assert {line.key for line in lines if line.status == "KNOWN"} == set(KNOWN)


# =============================================================================
# One load, build and refresh for the module
# =============================================================================
@dataclass(frozen=True)
class Built:
    """The database the module works on."""

    engine: Engine
    url: str


@pytest.fixture(scope="class")
def built(
    gold_engine: Engine, database_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Built:
    """Land the six workbooks, build Silver and refresh Gold."""
    loader = BronzeLoader(
        reader=CalamineWorkbookReader(),
        storage=LocalObjectStorage(tmp_path_factory.mktemp("gold_archive")),
        repository=PostgresBronzeLandingRepository(gold_engine),
        bucket="bronze-raw",
    )
    summary = load_all(loader, DATA_DIR, routes=EXACT_ROUTES)
    assert not [outcome.error for outcome in summary.outcomes if outcome.error]
    refresh_gold(with_silver=True, lock_timeout=60, engine=gold_engine)
    return Built(gold_engine, database_url)


@pytest.fixture
def connection(built: Built) -> Iterator[Connection]:
    """A connection whose writes are rolled back when the test ends."""
    with built.engine.connect() as opened:
        transaction = opened.begin()
        try:
            yield opened
        finally:
            transaction.rollback()


def _views(connection: Connection, month: date) -> Side:
    """The database side, from the Gold views."""
    drivers = {
        row.cuenta_stats: (row.cantidad, row.equs if row.suma_eq else None)
        for row in connection.execute(
            text("SELECT * FROM gold.v_resumen_equ WHERE mes = :m"), {"m": month}
        )
    }
    poly = dict(
        connection.execute(
            text(
                "SELECT cuenta, sum(valor_equ) FROM gold.inyeccion WHERE mes = :m GROUP BY cuenta"
            ),
            {"m": month},
        )
        .tuples()
        .all()
    )
    batt = connection.execute(
        text("SELECT * FROM gold.v_equs_batt_mes WHERE mes = :m"), {"m": month}
    ).one()
    totals = {
        "H32 EQUS POLY": sum(poly.values(), start=Decimal(0)),
        "H36 EQUS BATT": batt.equs_batt,
        "H38 EQUS OREC": batt.equs_orec,
        "H39 EQUS ENSAMBLE": batt.equs_ensamble,
        "H40 EQUS F&A": batt.equs_formacion + batt.equs_decorado,
        "H41 EQUS SHIPPING": batt.equs_shipping,
        "H44 EQUS FORMACION": batt.equs_formacion,
        "H45 EQUS DECORADO": batt.equs_decorado,
    }
    return Side(drivers, poly, totals)


def _from_report(report: dict[str, Any]) -> Side:
    """The database side, from the JSON of equs-report."""
    drivers: dict[str, tuple[Decimal, Decimal | None]] = {}
    totals: dict[str, Decimal] = {"H36 EQUS BATT": Decimal(str(report["equs_batt"]))}
    labels = {"OREC": "H38 EQUS OREC", "ENSAMBLE": "H39 EQUS ENSAMBLE", "F&A": "H40 EQUS F&A",
              "DESPACHOS": "H41 EQUS SHIPPING", "FORMACION": "H44 EQUS FORMACION",
              "DECORADO": "H45 EQUS DECORADO"}  # fmt: skip
    for plant in report["plantas"]:
        totals[labels[plant["planta"]]] = Decimal(str(plant["equs"]))
        for process in plant["procesos"]:
            if process["proceso"] in labels:
                totals[labels[process["proceso"]]] = Decimal(str(process["equs"]))
            for item in process["subsecciones"]:
                drivers[item["cuenta_stats"]] = (
                    Decimal(str(item["cantidad"])),
                    Decimal(str(item["equs"])),
                )
    for item in report["informativos"]:
        drivers[item["cuenta_stats"]] = (Decimal(str(item["cantidad"])), None)
    # Every driver of the version is listed, with 0 when it has no production.
    for cuenta in ABSENT_IN_DB:
        if drivers.get(cuenta, (Decimal(0), Decimal(0))) == (0, 0):
            drivers.pop(cuenta, None)
    drivers.pop("15BATPRD", None)
    poly = {
        item["cuenta"]: Decimal(str(item["equs"])) for item in report["equs_poly"]["subsecciones"]
    }
    totals["H32 EQUS POLY"] = Decimal(str(report["equs_poly"]["equs"]))
    return Side(drivers, poly, totals)


def _from_column_h(rows: list[dict[str, Any]]) -> Side:
    """The database side, from the JSON of resumen-equ."""
    drivers: dict[str, tuple[Decimal, Decimal | None]] = {}
    poly: dict[str, Decimal] = {}
    totals: dict[str, Decimal] = {}
    labels = {"EQUS BATT": "H36 EQUS BATT", "OREC": "H38 EQUS OREC",
              "ENSAMBLE": "H39 EQUS ENSAMBLE", "F&A": "H40 EQUS F&A",
              "DESPACHOS": "H41 EQUS SHIPPING", "FORMACION": "H44 EQUS FORMACION",
              "DECORADO": "H45 EQUS DECORADO", "EQUS POLY": "H32 EQUS POLY"}  # fmt: skip
    for row in rows:
        if row["tipo"] == "cuenta":
            equs = None if row["equs"] is None else Decimal(str(row["equs"]))
            drivers[row["nombre"]] = (Decimal(str(row["cantidad"])), equs)
        elif row["tipo"] == "poly":
            poly[row["nombre"]] = Decimal(str(row["equs"]))
        else:
            totals[labels[row["nombre"]]] = Decimal(str(row["equs"]))
    for cuenta in ABSENT_IN_DB:
        if drivers.get(cuenta, (Decimal(0), Decimal(0))) == (0, 0):
            drivers.pop(cuenta, None)
    drivers.pop("15BATPRD", None)
    return Side(drivers, poly, totals)


def _python() -> str:
    return Path(sys.executable).as_posix()


def _run(arguments: list[str], url: str, **environment: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed commands of this repository
        arguments,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=os.environ | {"DATABASE_URL": url, "LOG_LEVEL": "WARNING"} | environment,
        timeout=1800,
        check=False,
    )


def _make(target: str, url: str, *variables: str) -> subprocess.CompletedProcess[str]:
    assert MAKE is not None
    return _run(
        [MAKE, "--no-print-directory", target, "RUNNER=host", f"ENERGY={_python()} -m energy",
         f"DATABASE_URL={url}", *variables],
        url,
    )  # fmt: skip


def _valid(report: dict[str, Any]) -> dict[str, Any]:
    jsonschema.validate(report, load_schema())
    return report


class TestGoldOnTheWorkbooks:
    """Acceptance tests 2 to 16 and 18 of the Gold layer."""

    # -- Tests 2, 3 and 5: the documented figures -------------------------------

    def test_september_2026_equivalent_batteries(self, connection: Connection) -> None:
        """Test 2: EQUS_CONTEXT section 9.1, target column, within a cent."""
        batt = connection.execute(
            text("SELECT * FROM gold.v_equs_batt_mes WHERE mes = :m"), {"m": SEPTEMBER}
        ).one()
        poly = connection.execute(
            text("SELECT sum(valor_equ) FROM gold.inyeccion WHERE mes = :m"), {"m": SEPTEMBER}
        ).scalar_one()
        computed = {
            "OREC": batt.equs_orec,
            "ENSAMBLE": batt.equs_ensamble,
            "FORMACION": batt.equs_formacion,
            "DECORADO": batt.equs_decorado,
            "SHIPPING": batt.equs_shipping,
            "EQUS BATT": batt.equs_batt,
            "EQUS POLY": poly,
        }
        assert {key: _cents(value) for key, value in computed.items()} == SEPTEMBER_EQ

    def test_september_2026_quantities(self, connection: Connection) -> None:
        """Test 3: what every driver summed, EQUS_CONTEXT section 9.1."""
        rows = connection.execute(
            text("SELECT cuenta_stats, cantidad FROM gold.v_resumen_equ WHERE mes = :m"),
            {"m": SEPTEMBER},
        )
        quantities = dict(rows.tuples().all())
        assert {
            cuenta: quantities[cuenta].quantize(Decimal("0.001")) for cuenta in SEPTEMBER_QUANTITIES
        } == SEPTEMBER_QUANTITIES

    def test_every_month_of_the_fiscal_year(self, connection: Connection) -> None:
        """Test 5: EQUS_CONTEXT section 9.2 to the cent, and FY26."""
        rows = connection.execute(text("SELECT * FROM gold.v_equs_batt_mes ORDER BY mes")).all()
        computed = {
            row.mes.strftime("%Y-%m"): tuple(
                str(_cents(value))
                for value in (
                    row.equs_orec,
                    row.equs_ensamble,
                    row.equs_formacion,
                    row.equs_decorado,
                    row.equs_shipping,
                    row.equs_batt,
                )
            )
            for row in rows
        }
        assert computed == EQ_BY_MONTH
        assert round(sum(row.equs_batt for row in rows)) == 2_310_724

    # -- Test 4: column H of the workbook ---------------------------------------

    def test_column_h_reconciles_with_the_workbook(
        self, connection: Connection, workbook: Workbook
    ) -> None:
        """Test 4: row by row against RESUMEN EQU, read from EQUS BATT.xlsx."""
        assert workbook.month == SEPTEMBER
        _assert_reconciled(reconcile(workbook, _views(connection, workbook.month)))

    # -- Tests 6 to 9 -------------------------------------------------------------

    def test_a_new_version_changes_only_its_months(self, connection: Connection) -> None:
        """Test 6: version 2 from September with a truck multiplier of 2.5."""
        before = {
            row.mes: row.equs_ensamble
            for row in connection.execute(text("SELECT * FROM gold.v_equs_batt_mes"))
        }
        _add_version_2(connection)
        after = {
            row.mes: row.equs_ensamble
            for row in connection.execute(text("SELECT * FROM gold.v_equs_batt_mes"))
        }
        assert after[AUGUST] == before[AUGUST]
        truck = Decimal(16265) * (Decimal("2.5") - Decimal("2.15")) * Decimal("0.354")
        assert _cents(after[SEPTEMBER] - before[SEPTEMBER]) == _cents(truck)

    def test_close_freeze_and_reclose(self, built: Built, connection: Connection) -> None:
        """Tests 7 and 8: the close is written once, frozen, and replaced only on purpose."""
        summary = close_month(SEPTEMBER, closed_by="acceptance", lock_timeout=60,
                              engine=built.engine)  # fmt: skip
        assert summary.frozen.rows == 13
        assert _cents(summary.equs_batt) == SEPTEMBER_EQ["EQUS BATT"]
        assert _cents(summary.equs_poly) == SEPTEMBER_EQ["EQUS POLY"]
        with pytest.raises(CloseRefusedError, match="already closed"):
            close_month(SEPTEMBER, closed_by="again", lock_timeout=60, engine=built.engine)
        again = close_month(SEPTEMBER, closed_by="again", reclose=True, lock_timeout=60,
                            engine=built.engine)  # fmt: skip
        assert (again.frozen.reclosed, again.frozen.replaced) == (True, 13)

        # Test 8: a changed receipt moves the live view, never the close.
        connection.execute(
            text(
                "UPDATE silver.reporte_qad SET cambiar_cantidad_de_ubic = "
                "cambiar_cantidad_de_ubic + 1000 WHERE numero_de_transaccion = ("
                "SELECT min(numero_de_transaccion) FROM gold.v_reporte_qad "
                "WHERE mes = :m AND campo_equs_eq = 'OXIDO')"
            ),
            {"m": SEPTEMBER},
        )
        live = connection.execute(
            text(
                "SELECT cantidad FROM gold.v_resumen_equ WHERE mes = :m "
                "AND cuenta_stats = 'OXPROD_KG'"
            ),
            {"m": SEPTEMBER},
        ).scalar_one()
        frozen = connection.execute(
            text(
                "SELECT cantidad FROM gold.resumen_equ "
                "WHERE mes = :m AND cuenta_stats = 'OXPROD_KG'"
            ),
            {"m": SEPTEMBER},
        ).scalar_one()
        assert live == SEPTEMBER_QUANTITIES["OXPROD_KG"] + 1000
        assert frozen == SEPTEMBER_QUANTITIES["OXPROD_KG"]

        # Test 14: the live report now differs from the close and says so.
        report = _valid(build_report(connection, "2026-09-01", "2026-09-30"))
        assert "difiere_del_cierre" in [w["codigo"] for w in report["advertencias"]]
        closed = _valid(build_report(connection, "2026-09-01", "2026-09-30", source="closed"))
        assert closed["equs_batt"] == pytest.approx(float(SEPTEMBER_EQ["EQUS BATT"]), abs=0.01)
        assert [w["codigo"] for w in closed["advertencias"]] == ["poly_en_vivo"]

    def test_the_data_quality_views(self, connection: Connection) -> None:
        """Test 9: each runs; check 4 finds the covers and handles, check 5 the lines."""
        counts = dict(
            connection.execute(
                text(
                    "SELECT vista, filas FROM gold.calidad_datos_rango('2026-09-01', '2026-09-30')"
                )
            )
            .tuples()
            .all()
        )
        assert (counts["dq_sin_regla"], counts["dq_doble_regla"], counts["dq_sin_conversor"]) == (
            0,
            0,
            0,
        )
        poly = connection.execute(
            text(
                "SELECT count(*) AS filas, "
                "count(*) FILTER (WHERE linea_de_productos IN ('1200', '1202')) AS tapas_manijas "
                "FROM gold.dq_categoria_poly"
            )
        ).one()
        # EQUS_CONTEXT section 7 counts 1,495: the covers and handles of product
        # lines 1200 and 1202. The view also finds the 93 receipts of 150000015,
        # truck handles (ACCMED, line 1305), which carry EQ just the same.
        assert (poly.filas, poly.tapas_manijas) == (1_588, 1_495)
        lines = {
            row[0]
            for row in connection.execute(
                text("SELECT DISTINCT linea_produccion FROM gold.dq_linea_sin_planta")
            )
        }
        assert {"FADECO05", "POLINY05", "MAQBUJEO"} <= lines
        # Sixteen imported batteries have 0 decoration pieces in their structure.
        assert connection.execute(text("SELECT count(*) FROM gold.dq_sin_conversor")).scalar() == 34

    # -- Tests 10 to 15: the date-range report -------------------------------------

    def test_the_report_of_september_is_column_h(
        self, connection: Connection, workbook: Workbook
    ) -> None:
        """Test 10: the values of test 2 and 3, and the workbook's rows."""
        report = _valid(build_report(connection, "2026-09-01", "2026-09-30"))
        side = _from_report(report)
        for cuenta, quantity in SEPTEMBER_QUANTITIES.items():
            assert side.drivers[cuenta][0].quantize(Decimal("0.001")) == quantity
        assert _cents(side.totals["H36 EQUS BATT"]) == SEPTEMBER_EQ["EQUS BATT"]
        assert _cents(side.totals["H32 EQUS POLY"]) == SEPTEMBER_EQ["EQUS POLY"]
        assert report["equs_poly"]["piezas"] == 1_737_504
        assert report["equs_poly"]["piezas_con_equ"] == 1_631_925
        assert report["advertencias"] == []
        _assert_reconciled(reconcile(workbook, side))

    def test_partial_months_add_up(self, connection: Connection) -> None:
        """Test 11: the two halves of September."""
        first = _valid(build_report(connection, "2026-09-01", "2026-09-15"))
        second = _valid(build_report(connection, "2026-09-16", "2026-09-30"))
        whole = _valid(build_report(connection, "2026-09-01", "2026-09-30"))
        assert (first["equs_batt"], first["equs_poly"]["equs"]) == pytest.approx(
            (104_493.65, 239_626.44), abs=0.01
        )
        assert (second["equs_batt"], second["equs_poly"]["equs"]) == pytest.approx(
            (111_096.68, 263_406.75), abs=0.01
        )
        halves = (_from_report(first), _from_report(second))
        for cuenta, (cantidad, equs) in _from_report(whole).drivers.items():
            assert sum(h.drivers[cuenta][0] for h in halves) == pytest.approx(cantidad, abs=0.01)
            if equs is not None:
                assert sum(h.drivers[cuenta][1] or 0 for h in halves) == pytest.approx(
                    equs, abs=0.01
                )
        for half in (first, second):
            assert [w["codigo"] for w in half["advertencias"]] == ["mes_parcial"]

    def test_multi_month_ranges(self, connection: Connection) -> None:
        """Test 12: a quarter, the fiscal year, and the month-by-month breakdown."""
        quarter = _valid(build_report(connection, "2025-10-01", "2025-12-31"))
        # The sum of the three rounded rows of section 9.2 is 621,691.07; the
        # unrounded total is 621,691.078, within the cent the brief allows.
        assert abs(Decimal(str(quarter["equs_batt"])) - Decimal("621691.07")) <= CENT
        year = _valid(build_report(connection, "2025-10-01", "2026-09-30", by_month=True))
        assert round(year["equs_batt"]) == 2_310_724
        monthly = {
            row[0].strftime("%Y-%m"): float(row[1])
            for row in connection.execute(text("SELECT mes, equs_batt FROM gold.v_equs_batt_mes"))
        }
        assert {row["mes"]: row["equs_batt"] for row in year["por_mes"]} == pytest.approx(monthly)

    def test_a_range_across_two_versions(self, connection: Connection) -> None:
        """Test 13: August with version 1, September with version 2."""
        _add_version_2(connection)
        report = _valid(build_report(connection, "2026-08-01", "2026-09-30"))
        truck = next(
            item
            for plant in report["plantas"]
            for process in plant["procesos"]
            for item in process["subsecciones"]
            if item["cuenta_stats"] == "COSPRODU_TRUCK"
        )
        assert truck["factor"] is None
        assert [(v["version"], v["factor"]) for v in truck["por_version"]] == [
            (1, 0.7611),
            (2, 0.885),
        ]
        assert "varias_versiones" in [w["codigo"] for w in report["advertencias"]]

    def test_the_closed_source_refuses_what_is_not_closed(self, built: Built) -> None:
        """Test 14: half a closed month, or a month not closed, exit 1 naming the month."""
        for start, end, month in (
            ("2026-09-01", "2026-09-15", "2026-09"),
            ("2026-07-01", "2026-07-31", "2026-07"),
        ):
            finished = _run(
                [_python(), "-c", "from energy.__main__ import report_main; report_main()",
                 "--start", start, "--end", end, "--source", "closed"],
                built.url,
            )  # fmt: skip
            assert finished.returncode == 1, finished.stderr
            assert month in finished.stderr

    def test_edge_cases_of_the_range(self, connection: Connection) -> None:
        """Test 15: no data, no version, inverted, malformed."""
        empty = _valid(build_report(connection, "2030-01-01", "2030-01-31"))
        assert empty["equs_batt"] == 0
        drivers = [s for p in empty["plantas"] for x in p["procesos"] for s in x["subsecciones"]]
        assert all(item["cantidad"] == 0 for item in drivers)
        assert len(drivers) + len(empty["informativos"]) == 16
        with pytest.raises(InvalidPeriodError, match="no version"):
            build_report(connection, "2025-09-01", "2025-09-30")
        with pytest.raises(InvalidPeriodError):
            build_report(connection, "2026-09-30", "2026-09-01")
        with pytest.raises(InvalidPeriodError):
            build_report(connection, "2026-09-31", "2026-10-01")

    # -- Test 16: a refresh picks up a changed structure ----------------------------

    @pytest.mark.skipif(MAKE is None, reason="make is not installed")
    def test_make_refresh_picks_up_a_changed_structure(self, built: Built) -> None:
        """td_form is stale until `make refresh`; the close never moves."""
        engine = built.engine

        def september(cuenta: str, table: str = "gold.v_resumen_equ") -> tuple[Decimal, Decimal]:
            with engine.connect() as connection:
                row = connection.execute(
                    # The table is one of this test's two constants.
                    text(f"SELECT cantidad, equs FROM {table} "  # noqa: S608
                         "WHERE mes = :m AND cuenta_stats = :c"),
                    {"m": SEPTEMBER, "c": cuenta},
                ).one()  # fmt: skip
                return row[0], row[1]

        link = _paste_link_of(engine, "215000015")
        before = september("POS_ACT_MTL")
        frozen = _frozen_or_close(built)

        _set_link(engine, link, link.quantity * Decimal("1.5"))
        try:
            assert september("POS_ACT_MTL") == before
            refreshed = _make("refresh", built.url)
            assert refreshed.returncode == 0, refreshed.stderr
            assert september("POS_ACT_MTL")[0] != before[0]
            assert september("POS_ACT_MTL", "gold.resumen_equ") == frozen
        finally:
            _set_link(engine, link, link.quantity)
            restored = _make("refresh", built.url)
        assert restored.returncode == 0, restored.stderr
        cantidad, equs = september("POS_ACT_MTL")
        assert cantidad.quantize(Decimal("0.001")) == Decimal("648763.763")
        assert _cents(equs) == Decimal("27940.86")

    # -- Test 18: resumen-equ is column H --------------------------------------------

    def test_resumen_equ_is_column_h(self, built: Built, workbook: Workbook) -> None:
        """Test 18: the command's JSON has every row of test 4, with the same values."""
        finished = _run(
            [_python(), "-m", "energy", "equs", "resumen-equ", "--month", "2026-09",
             "--format", "json"],
            built.url,
        )  # fmt: skip
        assert finished.returncode == 0, finished.stderr
        rows = json.loads(finished.stdout)["filas"]
        _assert_reconciled(reconcile(workbook, _from_column_h(rows)))

    # -- The reporting schema Power BI reads ------------------------------------

    def test_the_reporting_views_give_the_documented_figures(self, connection: Connection) -> None:
        """Every FY26 month of section 9.2, September's POLY, and every receipt."""
        months = connection.execute(
            text(
                "SELECT mes, anio_fiscal, sum(equs) FILTER (WHERE suma_eq) AS equs_batt "
                "FROM reporting.equs_mensual GROUP BY mes, anio_fiscal ORDER BY mes"
            )
        ).all()
        assert {row.mes.strftime("%Y-%m"): str(_cents(row.equs_batt)) for row in months} == {
            month: figures[-1] for month, figures in EQ_BY_MONTH.items()
        }
        assert {row.anio_fiscal for row in months} == {"FY26"}
        poly = connection.execute(
            text("SELECT sum(equs) FROM reporting.equs_poly_mensual WHERE mes = :m"),
            {"m": SEPTEMBER},
        ).scalar_one()
        assert _cents(poly) == SEPTEMBER_EQ["EQUS POLY"]
        receipts = connection.execute(
            text("SELECT count(*) FROM reporting.recibos_produccion")
        ).scalar_one()
        assert receipts == 138_773
        differences = connection.execute(
            text(
                "SELECT count(*) FROM reporting.equs_mensual AS mensual "
                "LEFT JOIN (SELECT mes, cuenta_id, sum(equs) AS equs "
                "           FROM reporting.aporte_cuenta GROUP BY mes, cuenta_id) AS aporte "
                "       USING (mes, cuenta_id) "
                "WHERE coalesce(aporte.equs, 0) <> mensual.equs"
            )
        ).scalar_one()
        assert differences == 0


@dataclass(frozen=True)
class _Link:
    matriz: str
    componente: str
    quantity: Decimal


def _paste_link_of(engine: Engine, plate: str) -> _Link:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT enlace.articulo_matriz, enlace.articulo_componente, enlace.cantidad_por "
                "FROM silver.bom_estructura AS enlace "
                "JOIN silver.item_master AS pasta ON pasta.numero_articulo = "
                "enlace.articulo_componente "
                "WHERE enlace.articulo_matriz = :plate AND pasta.descripcion LIKE 'PASTA%' "
                "ORDER BY enlace.bom_id LIMIT 1"
            ),
            {"plate": plate},
        ).one()
    return _Link(row[0], row[1], row[2])


def _set_link(engine: Engine, link: _Link, quantity: Decimal) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE silver.bom_estructura SET cantidad_por = :q "
                "WHERE articulo_matriz = :m AND articulo_componente = :c"
            ),
            {"q": quantity, "m": link.matriz, "c": link.componente},
        )


def _frozen_or_close(built: Built) -> tuple[Decimal, Decimal]:
    """The frozen POS_ACT_MTL of September, closing the month if no test did."""
    with built.engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT cantidad, equs FROM gold.resumen_equ "
                "WHERE mes = :m AND cuenta_stats = 'POS_ACT_MTL'"
            ),
            {"m": SEPTEMBER},
        ).first()
    if row is None:
        close_month(SEPTEMBER, closed_by="acceptance", lock_timeout=60, engine=built.engine)
        return _frozen_or_close(built)
    return row[0], row[1]


def _add_version_2(connection: Connection) -> None:
    """Version 2 from September 2026: a truck battery counts 2.5 instead of 2.15."""
    connection.execute(
        text("UPDATE silver.equs_version SET vigente_hasta = '2026-08-31' WHERE version = 1")
    )
    connection.execute(
        text(
            "INSERT INTO silver.equs_version (version, nombre, vigente_desde) "
            "VALUES (2, 'STD FY27 TEST', '2026-09-01')"
        )
    )
    connection.execute(
        text(
            "INSERT INTO silver.factores_equ (version, cuenta_stats, proceso, descripcion, unidad, "
            "fuente, estandar_por_bat, multiplicador, peso, suma_eq) "
            "SELECT 2, cuenta_stats, proceso, descripcion, unidad, fuente, estandar_por_bat, "
            "CASE WHEN cuenta_stats = 'COSPRODU_TRUCK' THEN 2.5 ELSE multiplicador END, "
            "peso, suma_eq FROM silver.factores_equ WHERE version = 1"
        )
    )
    connection.execute(
        text(
            "INSERT INTO silver.instrucc (version, cuenta_stats, regla_n, linea_de_productos, "
            "campo_equs, campo_equs_excluir, prefijo_linea, signo) "
            "SELECT 2, cuenta_stats, regla_n, linea_de_productos, campo_equs, "
            "campo_equs_excluir, prefijo_linea, signo FROM silver.instrucc WHERE version = 1"
        )
    )


@pytest.mark.skipif(MAKE is None, reason="make is not installed")
class TestOneCommandRebuild:
    """Test 17: `make refresh-all` on an empty database, twice."""

    def test_refresh_all_builds_everything_and_again_changes_nothing(
        self, gold_engine: Engine, database_url: str, tmp_path: Path
    ) -> None:
        """Bronze, Silver and Gold from data/raw_excels; the second run lands nothing."""
        variables = ("ARCHIVE_BACKEND=local", f"ARCHIVE_DIR={tmp_path.as_posix()}")

        def bronze_rows() -> int:
            with gold_engine.connect() as connection:
                return connection.execute(
                    text(
                        "SELECT (SELECT count(*) FROM bronze.raw_rct_wo) "
                        "+ (SELECT count(*) FROM bronze.raw_facturas) "
                        "+ (SELECT count(*) FROM bronze.raw_product_struc) "
                        "+ (SELECT count(*) FROM bronze.raw_manual_sheets)"
                    )
                ).scalar_one()

        def months() -> list[tuple[Any, ...]]:
            with gold_engine.connect() as connection:
                return [
                    tuple(row)
                    for row in connection.execute(
                        text("SELECT * FROM gold.v_equs_batt_mes ORDER BY mes")
                    )
                ]

        timings: list[float] = []
        started = time.perf_counter()
        first = _make("refresh-all", database_url, *variables)
        timings.append(time.perf_counter() - started)
        assert first.returncode == 0, first.stderr[-3000:]
        landed = bronze_rows()
        numbers = months()

        shown = _make("resumen", database_url, "MONTH=2026-09")
        assert shown.returncode == 0, shown.stderr
        assert "215,590.33" in shown.stdout
        assert "503,033.19" in shown.stdout

        started = time.perf_counter()
        second = _make("refresh-all", database_url, *variables)
        timings.append(time.perf_counter() - started)
        assert second.returncode == 0, second.stderr[-3000:]
        assert bronze_rows() == landed
        assert months() == numbers
        print(f"\nmake refresh-all: {timings[0]:.1f} s, then {timings[1]:.1f} s")  # noqa: T201
