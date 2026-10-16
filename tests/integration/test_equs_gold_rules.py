"""The rules of EQUS Gold, on a handful of Silver rows written here.

Layer: tests (integration)

The acceptance tests in ``test_equs_gold_build.py`` check Gold against the real
workbooks, which only exist on the machines of the people who work with them.
These tests state each rule on its own instead, on a small world written
straight into Silver: an oxide receipt, a plate, two assembled batteries, a
formed and a decorated one, three plastic pieces, refined lead and a few
invoices. Every expected number below follows from those rows and the seeded
version 1 of the equation. They need no file from the business, so they run
everywhere.

Four classes, each on a database of its own: the views and the report, which
change nothing they keep; the daily view and the series of the battery
processes; the monthly close, which commits; and the refresh with its lock.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jsonschema
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from energy.adapters.postgres_equs_gold import PostgresGoldReader
from energy.domain.equs_gold import DateRange
from energy.domain.equs_series import totals
from energy.domain.exceptions import (
    CloseRefusedError,
    ConfigurationError,
    InvalidPeriodError,
    LockTimeoutError,
)
from energy.pipelines.flows.equs_gold import close_month, refresh_gold, resumen_equ
from energy.pipelines.flows.equs_report import build_report, load_schema
from energy.services.equs_gold import BatterySeries

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection, Engine

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SEPTEMBER = date(2026, 9, 1)
AUGUST = date(2026, 8, 1)

# (item, description, product line, Campo Equs, polarity)
ITEMS: tuple[tuple[str, str, str, str, str | None], ...] = (
    ("211000003", "PASTA POSITIVA", "2101", "N/A", "+"),
    ("211000004", "PASTA NEGATIVA", "2101", "N/A", "-"),
    ("211000001", "OXIDO DE PLOMO", "2101", "OXIDO", None),
    ("216000006", "PLACA (+) SOV", "2106", "PLACASLI", "+"),
    ("216000007", "PLACA (-) SOV", "2106", "PLACASLI", "-"),
    ("221000001", "BE 75 6*5", "2201", "SLI-S", None),
    ("223000001", "BE 4D TRUCK", "2201", "TRUCK-B", None),
    ("231000001", "BF 75 6*5", "3101", "SLI-S", None),
    ("311000006", "BT 75 DECORADA", "3103", "SLI-S", None),
    ("171000001", "ETIQUETA 75", "1401", "N/A", None),
    ("121000001", "TAPA BORNE", "1200", "N/A", None),
    ("121000442", "CUB 1P 75", "1200", "N/A", None),
    ("131000001", "CAJA SLI 75", "1201", "CAJA SLI", None),
    ("157000012", "PELLET", "1308", "N/A", None),
    ("113000001", "PLOMO REFINADO", "1103", "N/A", None),
)

# (line, plant area); FADECO05 has none, as in the real data.
LINES: tuple[tuple[str, str | None], ...] = (
    ("OXIDO01", "OREC"),
    ("PLACAS01", "OREC"),
    ("ENSLIN01", "Ensamble"),
    ("FAFORM01", "F&A"),
    ("FADECO01", "F&A"),
    ("FADECO05", None),
    ("POLINY01", "Componentes"),
    ("RCREFI01", "RC"),
)

# (parent, component, quantity per): paste per plate, plates per assembled
# battery, the assembled battery of the formed one, the pieces of the decorated.
LINKS: tuple[tuple[str, str, str], ...] = (
    ("216000006", "211000003", "0.0636"),
    ("216000007", "211000004", "0.066"),
    ("221000001", "216000006", "36"),
    ("221000001", "216000007", "30"),
    ("231000001", "221000001", "1"),
    ("311000006", "171000001", "1"),
    ("311000006", "121000001", "2"),
)

# (transaction, date, item, warehouse, line, quantity, Campo Equs on the receipt)
RECEIPTS: tuple[tuple[int, str, str, str, str | None, str, str | None], ...] = (
    (1, "2026-09-05", "211000001", "1515", "OXIDO01", "1000", "OXIDO"),
    (2, "2026-09-05", "216000006", "1515", "PLACAS01", "840", "PLACASLI"),
    (3, "2026-09-06", "221000001", "1515", "ENSLIN01", "100", "SLI-S"),
    # No Campo Equs on the receipt: the item master's TRUCK-B applies.
    (4, "2026-09-06", "223000001", "1515", "ENSLIN01", "10", None),
    (5, "2026-09-07", "231000001", "1515", "FAFORM01", "50", "SLI-S"),
    (6, "2026-09-08", "311000006", "1515", "FADECO01", "40", "SLI-S"),
    # Plastic injection: a cover QAD marks N/A, a container, and the pellet.
    (7, "2026-09-09", "121000442", "1010", "POLINY01", "1000", "N/A"),
    (8, "2026-09-09", "131000001", "1010", "POLINY01", "200", "CAJA SLI"),
    (9, "2026-09-09", "157000012", "1010", "POLINY01", "300", "N/A"),
    (10, "2026-09-10", "113000001", "1520", "RCREFI01", "5000", "N/A"),
    # The second half of September.
    (11, "2026-09-20", "211000001", "1515", "OXIDO01", "500", "OXIDO"),
    # August.
    (12, "2026-08-10", "211000001", "1515", "OXIDO01", "2000", "OXIDO"),
    (13, "2026-08-10", "221000001", "1515", "ENSLIN01", "100", "SLI-S"),
)

# (invoice, date, channel, quantity): only sales count, whatever the case, and a
# credit note subtracts.
INVOICES: tuple[tuple[str, str, str, str], ...] = (
    ("F1", "2026-09-05", "VENTA", "30"),
    ("F2", "2026-09-25", "venta", "5"),
    ("F3", "2026-09-26", "Venta", "-2"),
    ("F4", "2026-09-27", "Exportación", "7"),
)

# What September sums, driver by driver.
SEPTEMBER_QUANTITIES: dict[str, Decimal] = {
    "OXPROD_KG": Decimal(1500),
    "PLATEPRD_SLI": Decimal(840),
    "COSPRODU_SLI": Decimal(100),
    "COSPRODU_TRUCK": Decimal(10),
    # 50 formed batteries x (36 x 0.0636) kg of positive paste.
    "POS_ACT_MTL": Decimal("114.48"),
    "FORM_PRD_SLI": Decimal(50),
    # 40 decorated batteries x (1 label + 2 caps).
    "COMP_DEC": Decimal(120),
    "UNITSHIP": Decimal(33),
    "LEADPROD": Decimal(5000),
}


def _seed(connection: Connection) -> None:
    """Write the small world into Silver and refresh the conversion tables."""
    run_id = connection.execute(
        text(
            "INSERT INTO bronze.ingestion_runs (flow_name, source_key, source_system, plant_code) "
            "VALUES ('gold rules', 'qad_rct_wo', 'qad', '1515') RETURNING run_id"
        )
    ).scalar_one()

    def insert(statement: str, rows: list[dict[str, Any]]) -> None:
        connection.execute(text(statement), rows)

    insert(
        "INSERT INTO silver.almacen (almac) VALUES (:almac)",
        [{"almac": almac} for almac in ("1515", "1010", "1520")],
    )
    insert(
        "INSERT INTO silver.linea_de_productos (linea_de_productos) VALUES (:linea) "
        "ON CONFLICT DO NOTHING",
        [{"linea": linea} for linea in {item[2] for item in ITEMS} | {"2105"}],
    )
    insert(
        "INSERT INTO silver.campo_equs (campo_equs) VALUES (:campo) ON CONFLICT DO NOTHING",
        [{"campo": campo} for campo in ("N/A", "OXIDO", "PLACASLI", "SLI-S", "TRUCK-B",
                                        "CAJA SLI", "CUB 1P", "PELLET", "NUEVO")],
    )  # fmt: skip
    connection.execute(text("INSERT INTO silver.planta (planta, nombre) VALUES ('RC', 'RC')"))
    insert(
        "INSERT INTO silver.lineas_produccion (linea, planta) VALUES (:linea, :planta)",
        [{"linea": linea, "planta": planta} for linea, planta in LINES],
    )
    insert(
        "INSERT INTO silver.item_master "
        "(numero_articulo, descripcion, linea_de_productos, campo_equs, polaridad) "
        "VALUES (:item, :descripcion, :linea, :campo, :polaridad)",
        [
            {"item": i, "descripcion": d, "linea": li, "campo": c, "polaridad": p}
            for i, d, li, c, p in ITEMS
        ],
    )
    insert(
        "INSERT INTO silver.bom_estructura (articulo_matriz, articulo_componente, cantidad_por, "
        "source_sheet, source_run_id) VALUES (:matriz, :componente, :cantidad, 'rules', :run)",
        [
            {"matriz": m, "componente": c, "cantidad": Decimal(q), "run": run_id}
            for m, c, q in LINKS
        ],
    )
    insert(
        "INSERT INTO silver.element_deco (numero_articulo, elemento_deco) VALUES (:item, true)",
        [{"item": "171000001"}, {"item": "121000001"}],
    )
    insert(
        "INSERT INTO silver.clasif_poly (numero_articulo, campo_equs, cuenta_stats, factor_equ) "
        "VALUES (:item, :campo, :cuenta, :factor)",
        [
            {"item": "121000442", "campo": "CUB 1P", "cuenta": "COVRPROD_SLI1", "factor": 0.51},
            {"item": "131000001", "campo": "CAJA SLI", "cuenta": "CONTPROD_CAR", "factor": 1},
            {"item": "157000012", "campo": "PELLET", "cuenta": "NA", "factor": 0},
        ],
    )
    insert(
        "INSERT INTO silver.reporte_qad (numero_de_transaccion, tipo_transaccion, fecha_efectiva, "
        "numero_articulo, almac, linea_produccion, cambiar_cantidad_de_ubic, campo_equs, "
        "source_run_id) VALUES (:n, 'RCT-WO', :fecha, :item, :almac, :linea, :cantidad, "
        ":campo, :run)",
        [
            {
                "n": n,
                "fecha": date.fromisoformat(fecha),
                "item": item,
                "almac": almac,
                "linea": linea,
                "cantidad": Decimal(cantidad),
                "campo": campo,
                "run": run_id,
            }
            for n, fecha, item, almac, linea, cantidad, campo in RECEIPTS
        ],
    )
    insert(
        "INSERT INTO silver.unit_ship (num_fact, orden, linea, canal, fecha_efectiva, "
        "numero_articulo, cantidad_fac, source_run_id) "
        "VALUES (:factura, 'O1', 1, :canal, :fecha, '311000006', :cantidad, :run)",
        [
            {
                "factura": factura,
                "canal": canal,
                "fecha": date.fromisoformat(fecha),
                "cantidad": Decimal(cantidad),
                "run": run_id,
            }
            for factura, fecha, canal, cantidad in INVOICES
        ],
    )
    connection.execute(text("REFRESH MATERIALIZED VIEW silver.td_form"))
    connection.execute(text("REFRESH MATERIALIZED VIEW silver.td_deco"))


@pytest.fixture(scope="class")
def seeded(gold_engine: Engine) -> Engine:
    """A database at head holding the small world, committed."""
    with gold_engine.begin() as connection:
        _seed(connection)
    return gold_engine


@pytest.fixture
def connection(seeded: Engine) -> Iterator[Connection]:
    """A connection whose writes are rolled back when the test ends."""
    with seeded.connect() as opened:
        transaction = opened.begin()
        try:
            yield opened
        finally:
            transaction.rollback()


def _factors(connection: Connection, version: int = 1) -> dict[str, Decimal]:
    rows = connection.execute(
        text("SELECT cuenta_stats, factor FROM silver.factores_equ WHERE version = :v"),
        {"v": version},
    )
    return dict(rows.tuples().all())


def _month(connection: Connection, month: date) -> dict[str, tuple[Decimal, Decimal]]:
    rows = connection.execute(
        text("SELECT cuenta_stats, cantidad, equs FROM gold.v_resumen_equ WHERE mes = :mes"),
        {"mes": month},
    )
    return {row[0]: (row[1], row[2]) for row in rows}


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


def _valid(report: dict[str, Any]) -> dict[str, Any]:
    jsonschema.validate(report, load_schema())
    return report


def _subsection(report: dict[str, Any], cuenta: str) -> dict[str, Any]:
    for plant in report["plantas"]:
        for process in plant["procesos"]:
            for item in process["subsecciones"]:
                if item["cuenta_stats"] == cuenta:
                    return item
    for item in report["informativos"]:
        if item["cuenta_stats"] == cuenta:
            return item
    raise AssertionError(cuenta)


class TestGoldViews:
    """The calculation views, the data-quality views and the date-range report."""

    def test_the_receipts_carry_the_columns_the_workbook_calculates(
        self, connection: Connection
    ) -> None:
        """Paste, decoration pieces, and the plastic-injection EQ of warehouse 1010 only."""
        rows = {
            row.numero_de_transaccion: row
            for row in connection.execute(text("SELECT * FROM gold.v_reporte_qad"))
        }
        assert rows[5].mat_activo == Decimal("114.48")
        assert rows[6].comp_deco == 120
        assert rows[4].campo_equs_eq == "TRUCK-B"
        assert (rows[7].new_equ, rows[7].cuenta, rows[7].valor_equ) == (
            "CUB 1P",
            "COVRPROD_SLI1",
            Decimal(510),
        )
        assert rows[9].valor_equ == 0
        assert all(row.new_equ is None for n, row in rows.items() if n not in (7, 8, 9))
        assert rows[1].mes == SEPTEMBER

    def test_each_driver_sums_what_its_rules_select(self, connection: Connection) -> None:
        """Q_k of September; drivers whose rules matched nothing have no row."""
        month = _month(connection, SEPTEMBER)
        assert {cuenta: cantidad for cuenta, (cantidad, _) in month.items()} == (
            SEPTEMBER_QUANTITIES
        )
        factors = _factors(connection)
        assert all(equs == cantidad * factors[c] for c, (cantidad, equs) in month.items())

    def test_the_totals_of_a_month(self, connection: Connection) -> None:
        """EQUS BATT adds every driver that adds; informative ones add nothing."""
        factors = _factors(connection)
        expected = {
            proceso: sum((SEPTEMBER_QUANTITIES[c] * factors[c] for c in cuentas), start=Decimal(0))
            for proceso, cuentas in {
                "OREC": ("OXPROD_KG", "PLATEPRD_SLI"),
                "ENSAMBLE": ("COSPRODU_SLI", "COSPRODU_TRUCK"),
                "FORMACION": ("POS_ACT_MTL",),
                "DECORADO": ("COMP_DEC",),
                "SHIPPING": ("UNITSHIP",),
            }.items()
        }
        row = connection.execute(
            text("SELECT * FROM gold.v_equs_batt_mes WHERE mes = :mes"), {"mes": SEPTEMBER}
        ).one()
        assert row.equs_orec == expected["OREC"]
        assert row.equs_ensamble == expected["ENSAMBLE"]
        assert row.equs_formacion == expected["FORMACION"]
        assert row.equs_decorado == expected["DECORADO"]
        assert row.equs_shipping == expected["SHIPPING"]
        assert row.equs_batt == sum(expected.values())

    def test_the_equation_is_data(self, connection: Connection) -> None:
        """A changed multiplier or a new rule changes Gold; no view knows the numbers."""
        before = _month(connection, SEPTEMBER)
        connection.execute(
            text(
                "UPDATE silver.factores_equ SET multiplicador = multiplicador * 2 "
                "WHERE version = 1 AND cuenta_stats = 'OXPROD_KG'"
            )
        )
        connection.execute(
            text(
                "INSERT INTO silver.instrucc (version, cuenta_stats, regla_n, prefijo_linea) "
                "VALUES (1, 'LEADPROD', 2, 'OXIDO')"
            )
        )
        after = _month(connection, SEPTEMBER)
        assert abs(after["OXPROD_KG"][1] - 2 * before["OXPROD_KG"][1]) < Decimal("1e-9")
        assert after["LEADPROD"][0] == before["LEADPROD"][0] + 1500

    def test_a_negative_rule_subtracts(self, connection: Connection) -> None:
        """The sign of a rule is applied to what it selects."""
        connection.execute(
            text(
                "INSERT INTO silver.instrucc (version, cuenta_stats, regla_n, campo_equs, signo) "
                "VALUES (1, 'LEADPROD', 2, 'OXIDO', -1)"
            )
        )
        assert _month(connection, SEPTEMBER)["LEADPROD"][0] == 5000 - 1500

    def test_each_month_uses_the_version_in_force(self, connection: Connection) -> None:
        """Version 2 from September: August keeps version 1, September uses 2."""
        _add_version_2(connection)
        september = connection.execute(
            text(
                "SELECT version, equs FROM gold.v_resumen_equ "
                "WHERE mes = :mes AND cuenta_stats = 'COSPRODU_TRUCK'"
            ),
            {"mes": SEPTEMBER},
        ).one()
        assert september.version == 2
        assert september.equs == Decimal(10) * Decimal("2.5") * Decimal("0.354")
        august = connection.execute(
            text("SELECT DISTINCT version FROM gold.v_resumen_equ WHERE mes = :mes"),
            {"mes": AUGUST},
        ).all()
        assert [row[0] for row in august] == [1]

    def test_only_sales_count_as_shipped(self, connection: Connection) -> None:
        """Channel Venta in any case, net of the credit note; exports are left out."""
        rows = connection.execute(
            text("SELECT num_fact, valor FROM gold.v_unit_ship_regla ORDER BY num_fact")
        ).all()
        assert [tuple(row) for row in rows] == [
            ("F1", Decimal(30)),
            ("F2", Decimal(5)),
            ("F3", Decimal(-2)),
        ]

    def test_the_pivots_of_the_workbook(self, connection: Connection) -> None:
        """LINEAS DE PDCTO by month, and INYECCION (EQUS POLY) by category."""
        lineas = connection.execute(
            text(
                "SELECT linea_de_productos, campo_equs, cantidad, mat_activo, comp_deco "
                "FROM gold.lineas_de_pdcto WHERE mes = :mes AND linea_de_productos IN "
                "('3101', '3103', '2201') ORDER BY 1, 2"
            ),
            {"mes": SEPTEMBER},
        ).all()
        assert [tuple(row) for row in lineas] == [
            ("2201", "SLI-S", Decimal(100), None, None),
            ("2201", "TRUCK-B", Decimal(10), None, None),
            ("3101", "SLI-S", Decimal(50), Decimal("114.48"), None),
            ("3103", "SLI-S", Decimal(40), None, Decimal(120)),
        ]
        poly = connection.execute(
            text(
                "SELECT cuenta, new_equ, piezas, valor_equ FROM gold.inyeccion "
                "WHERE mes = :mes ORDER BY cuenta"
            ),
            {"mes": SEPTEMBER},
        ).all()
        assert [tuple(row) for row in poly] == [
            ("CONTPROD_CAR", "CAJA SLI", Decimal(200), Decimal(200)),
            ("COVRPROD_SLI1", "CUB 1P", Decimal(1000), Decimal(510)),
            ("NA", "PELLET", Decimal(300), Decimal(0)),
        ]

    def test_conversion_cost_per_equivalent_battery(self, connection: Connection) -> None:
        """Cost summed over plant areas, divided by the EQ; null without a cost."""
        connection.execute(
            text(
                "INSERT INTO silver.costo_conversion (mes, planta, proceso, costo_cop) VALUES "
                "('2026-09-01', 'OREC', 'OREC', 1000000), ('2026-09-01', 'RC', 'OREC', 500000)"
            )
        )
        rows = {
            row.proceso: row
            for row in connection.execute(
                text("SELECT * FROM gold.v_costo_por_eq WHERE mes = :mes"), {"mes": SEPTEMBER}
            )
        }
        assert rows["OREC"].costo_cop == 1_500_000
        assert rows["OREC"].cop_por_eq == rows["OREC"].costo_cop / rows["OREC"].equs
        assert rows["ENSAMBLE"].cop_por_eq is None

    # -- Data-quality checks ---------------------------------------------------

    @staticmethod
    def _receipt(connection: Connection, n: int, item: str, line: str = "ENSLIN01") -> None:
        connection.execute(
            text(
                "INSERT INTO silver.reporte_qad (numero_de_transaccion, tipo_transaccion, "
                "fecha_efectiva, numero_articulo, almac, linea_produccion, "
                "cambiar_cantidad_de_ubic, source_run_id) "
                "SELECT :n, 'RCT-WO', '2026-09-15', :item, '1515', :line, 7, source_run_id "
                "FROM silver.reporte_qad LIMIT 1"
            ),
            {"n": n, "item": item, "line": line},
        )

    @staticmethod
    def _item(connection: Connection, item: str, line: str, campo: str) -> None:
        connection.execute(
            text(
                "INSERT INTO silver.item_master (numero_articulo, descripcion, "
                "linea_de_productos, campo_equs) VALUES (:item, 'NEW', :line, :campo)"
            ),
            {"item": item, "line": line, "campo": campo},
        )

    @staticmethod
    def _rows(connection: Connection, view: str) -> list[Any]:
        # The names are this module's own.
        return list(connection.execute(text(f"SELECT * FROM gold.{view}")))  # noqa: S608

    def test_the_small_world_is_clean_where_it_should_be(self, connection: Connection) -> None:
        """No receipt lacks a rule, none matches two, none lacks its conversion."""
        for view in ("dq_sin_regla", "dq_doble_regla", "dq_sin_conversor"):
            assert self._rows(connection, view) == [], view

    def test_check_1_finds_a_process_level_receipt_no_rule_takes(
        self, connection: Connection
    ) -> None:
        """A new Campo Equs on a plate line; lead outside 2101-3104 is not checked."""
        self._item(connection, "215999999", "2105", "NUEVO")
        self._receipt(connection, 100, "215999999")
        rows = self._rows(connection, "dq_sin_regla")
        assert [(row.numero_de_transaccion, row.campo_equs_eq) for row in rows] == [(100, "NUEVO")]

    def test_check_2_finds_a_receipt_two_rules_of_a_process_take(
        self, connection: Connection
    ) -> None:
        """Oxide counted twice in OREC."""
        connection.execute(
            text(
                "INSERT INTO silver.instrucc (version, cuenta_stats, regla_n, linea_de_productos) "
                "VALUES (1, 'OXPROD_KG', 2, '2101')"
            )
        )
        rows = self._rows(connection, "dq_doble_regla")
        assert {row.numero_de_transaccion for row in rows} == {1, 11, 12}
        assert {row.reglas for row in rows} == {"OXPROD_KG #1, OXPROD_KG #2"}

    def test_check_3_finds_batteries_without_their_conversion(self, connection: Connection) -> None:
        """A formed battery with no paste in td_form."""
        self._item(connection, "231999999", "3101", "SLI-S")
        self._receipt(connection, 101, "231999999", "FAFORM01")
        rows = self._rows(connection, "dq_sin_conversor")
        assert [(row.numero_articulo, row.fuente) for row in rows] == [("231999999", "MAT_ACTIVO")]

    def test_check_4_finds_plastic_pieces_qad_does_not_classify(
        self, connection: Connection
    ) -> None:
        """The cover QAD marks N/A; the pellet, whose factor is 0, is not a finding."""
        rows = self._rows(connection, "dq_categoria_poly")
        assert [(row.numero_articulo, row.campo_equs_qad, row.categoria_poly) for row in rows] == [
            ("121000442", "N/A", "CUB 1P")
        ]

    def test_check_5_finds_receipts_of_lines_without_plant_area(
        self, connection: Connection
    ) -> None:
        """FADECO05 belongs to no plant area."""
        self._receipt(connection, 102, "311000006", "FADECO05")
        rows = self._rows(connection, "dq_linea_sin_planta")
        assert [(row.linea_produccion, row.numero_de_transaccion) for row in rows] == [
            ("FADECO05", 102)
        ]

    def test_check_6_reads_its_threshold_from_the_session(self, connection: Connection) -> None:
        """Oxide fell 25 %: not a finding at 30 %, a finding at 20 %."""
        default = {row.cuenta_stats for row in self._rows(connection, "dq_variacion")}
        assert "OXPROD_KG" not in default
        # Drivers that start in September (from nothing in August) always move.
        assert {"PLATEPRD_SLI", "COSPRODU_TRUCK"} <= default
        connection.execute(text("SELECT set_config('equs.umbral_variacion', '0.20', true)"))
        lower = {row.cuenta_stats: row for row in self._rows(connection, "dq_variacion")}
        assert lower["OXPROD_KG"].variacion == Decimal("-0.25")

    def test_quality_counts_respect_the_range(self, connection: Connection) -> None:
        """Counted by the date of each receipt, or by month for check 6."""
        self._receipt(connection, 102, "311000006", "FADECO05")
        counts = dict(
            connection.execute(
                text("SELECT vista, filas FROM gold.calidad_datos_rango(:a, :b)"),
                {"a": date(2026, 9, 16), "b": date(2026, 9, 30)},
            )
            .tuples()
            .all()
        )
        assert counts["dq_linea_sin_planta"] == 0
        assert counts["dq_categoria_poly"] == 0
        assert counts["dq_variacion"] > 0
        counts = dict(
            connection.execute(
                text("SELECT vista, filas FROM gold.calidad_datos_rango(:a, :b)"),
                {"a": SEPTEMBER, "b": date(2026, 9, 15)},
            )
            .tuples()
            .all()
        )
        assert (counts["dq_linea_sin_planta"], counts["dq_categoria_poly"]) == (1, 1)

    # -- Date ranges -----------------------------------------------------------

    def test_a_whole_month_by_range_is_the_month(self, connection: Connection) -> None:
        """resumen_equ_rango and v_resumen_equ cannot diverge; every driver appears."""
        rows = connection.execute(
            text("SELECT * FROM gold.resumen_equ_rango(:a, :b)"),
            {"a": SEPTEMBER, "b": date(2026, 9, 30)},
        ).all()
        month = _month(connection, SEPTEMBER)
        assert len(rows) == 16
        for row in rows:
            assert (row.cantidad, row.equs) == month.get(row.cuenta_stats, (0, 0)), row

    def test_two_halves_add_up_to_the_month(self, connection: Connection) -> None:
        """Split by the date of each row, invoices included."""

        def totals(start: date, end: date) -> dict[str, Decimal]:
            rows = connection.execute(
                text("SELECT cuenta_stats, cantidad FROM gold.resumen_equ_rango(:a, :b)"),
                {"a": start, "b": end},
            )
            return dict(rows.tuples().all())

        first = totals(SEPTEMBER, date(2026, 9, 15))
        second = totals(date(2026, 9, 16), date(2026, 9, 30))
        assert first["OXPROD_KG"] == 1000
        assert second["OXPROD_KG"] == 500
        assert (first["UNITSHIP"], second["UNITSHIP"]) == (30, 3)
        whole = totals(SEPTEMBER, date(2026, 9, 30))
        assert {c: first[c] + second[c] for c in whole} == whole

    def test_the_report_of_a_month(self, connection: Connection) -> None:
        """Laid out as RESUMEN EQU; every driver present; valid against the schema."""
        report = _valid(build_report(connection, "2026-09-01", "2026-09-30"))

        assert [plant["planta"] for plant in report["plantas"]] == [
            "OREC",
            "ENSAMBLE",
            "F&A",
            "DESPACHOS",
        ]
        fya = report["plantas"][2]
        assert [process["proceso"] for process in fya["procesos"]] == ["FORMACION", "DECORADO"]
        assert [item["cuenta_stats"] for item in fya["procesos"][1]["subsecciones"]] == [
            "COMP_DEC",
            "CARTON_BATT",
        ]
        assert _subsection(report, "GRIDS_REG_POS")["cantidad"] == 0
        assert _subsection(report, "POS_ACT_MTL")["cantidad"] == 114.48
        assert [item["cuenta_stats"] for item in report["informativos"]] == [
            "15BATPRD",
            "FORM_PRD_SLI",
            "FORM_PRD_TRUCK",
            "LEADPROD",
        ]
        assert report["plantas"][0]["peso"] == 0.346
        assert report["plantas"][3]["area_planta"] is None
        assert report["equs_poly"] == {
            "area_planta": "Componentes",
            "piezas": 1500,
            "piezas_con_equ": 1200,
            "equs": 710,
            "subsecciones": [
                {"fila": 20, "cuenta": "CONTPROD_CAR", "categoria": "CAJA SLI", "piezas": 200,
                 "factor_equ": 1, "equs": 200},
                {"fila": 23, "cuenta": "COVRPROD_SLI1", "categoria": "CUB 1P", "piezas": 1000,
                 "factor_equ": 0.51, "equs": 510},
                {"fila": None, "cuenta": "NA", "categoria": "PELLET", "piezas": 300,
                 "factor_equ": 0, "equs": 0},
            ],
        }  # fmt: skip
        total = connection.execute(
            text("SELECT equs_batt FROM gold.v_equs_batt_mes WHERE mes = :mes"),
            {"mes": SEPTEMBER},
        ).scalar_one()
        assert report["equs_batt"] == pytest.approx(float(total), abs=1e-6)
        assert report["advertencias"] == []
        assert report["calidad_datos"]["dq_categoria_poly"] == 1

    def test_a_partial_range_is_flagged(self, connection: Connection) -> None:
        """Half a month carries the partial-month warning."""
        report = _valid(build_report(connection, "2026-09-16", "2026-09-30"))
        assert [w["codigo"] for w in report["advertencias"]] == ["mes_parcial"]
        assert report["advertencias"][0]["meses"] == ["2026-09"]
        assert report["rango"]["dias"] == 15

    def test_the_month_by_month_breakdown(self, connection: Connection) -> None:
        """Each subsection, process and plant gets its months; they add up."""
        report = _valid(build_report(connection, "2026-08-01", "2026-09-30", by_month=True))
        oxide = _subsection(report, "OXPROD_KG")
        assert [(month["mes"], month["cantidad"]) for month in oxide["por_mes"]] == [
            ("2026-08", 2000),
            ("2026-09", 1500),
        ]
        months = {row["mes"]: row["equs_batt"] for row in report["por_mes"]}
        totals = dict(
            connection.execute(
                text("SELECT to_char(mes, 'YYYY-MM'), equs_batt FROM gold.v_equs_batt_mes")
            )
            .tuples()
            .all()
        )
        assert months == pytest.approx({month: float(value) for month, value in totals.items()})

    def test_a_range_across_two_versions(self, connection: Connection) -> None:
        """Each month with its own version; the factor of a driver is given per version."""
        _add_version_2(connection)
        report = _valid(build_report(connection, "2026-08-01", "2026-09-30"))
        truck = _subsection(report, "COSPRODU_TRUCK")
        assert truck["factor"] is None
        assert [(v["version"], v["factor"], v["cantidad"]) for v in truck["por_version"]] == [
            (1, 0.7611, 0),
            (2, 0.885, 10),
        ]
        assert [w["codigo"] for w in report["advertencias"]] == ["varias_versiones"]
        assert [(v["version"], v["meses"]) for v in report["versiones"]] == [
            (1, ["2026-08"]),
            (2, ["2026-09"]),
        ]

    def test_ranges_that_have_no_answer(self, connection: Connection) -> None:
        """No version, a start after the end, a bad date, overlapping versions."""
        with pytest.raises(InvalidPeriodError, match="no version"):
            build_report(connection, "2025-09-01", "2025-09-30")
        with pytest.raises(InvalidPeriodError, match="later than the end"):
            build_report(connection, "2026-09-30", "2026-09-01")
        with pytest.raises(InvalidPeriodError, match="YYYY-MM-DD"):
            build_report(connection, "2026-9-1", "2026-09-30")
        connection.execute(
            text(
                "INSERT INTO silver.equs_version (version, nombre, vigente_desde) "
                "VALUES (3, 'OVERLAP', '2026-09-01')"
            )
        )
        with pytest.raises(ConfigurationError, match="two versions"):
            build_report(connection, "2026-09-01", "2026-09-30")

    def test_a_range_without_data_lists_every_driver_at_zero(self, connection: Connection) -> None:
        """Version 1 is open-ended, so 2030 has an equation and no production."""
        report = _valid(build_report(connection, "2030-01-01", "2030-01-31"))
        assert report["equs_batt"] == 0
        assert report["equs_poly"]["subsecciones"] == []
        drivers = [s for p in report["plantas"] for x in p["procesos"] for s in x["subsecciones"]]
        assert len(drivers) + len(report["informativos"]) == 16

    def test_the_frozen_close_answers_only_closed_whole_months(
        self, connection: Connection
    ) -> None:
        """Nothing is closed in this database."""
        with pytest.raises(InvalidPeriodError, match="whole months that are all closed"):
            build_report(connection, "2026-09-01", "2026-09-30", source="closed")


def _days(connection: Connection, cuenta: str) -> list[tuple[date, int, Decimal]]:
    rows = connection.execute(
        text(
            "SELECT fecha_efectiva, version, cantidad FROM gold.v_resumen_equ_diario "
            "WHERE cuenta_stats = :cuenta ORDER BY fecha_efectiva"
        ),
        {"cuenta": cuenta},
    )
    return [tuple(row) for row in rows]  # type: ignore[misc]


def _series(connection: Connection, start: date, end: date, **options: Any) -> Any:
    reader = PostgresGoldReader(connection)
    return BatterySeries(reader, reader).series(DateRange(start, end), **options)


class TestDailyEquivalents:
    """The daily view (revision 0006) and the series of the battery processes."""

    def test_each_day_sums_the_rows_of_its_accounting_date(self, connection: Connection) -> None:
        """Oxide on two days of September and one of August; sales net of the credit note."""
        assert _days(connection, "OXPROD_KG") == [
            (date(2026, 8, 10), 1, Decimal(2000)),
            (date(2026, 9, 5), 1, Decimal(1000)),
            (date(2026, 9, 20), 1, Decimal(500)),
        ]
        assert _days(connection, "UNITSHIP") == [
            (date(2026, 9, 5), 1, Decimal(30)),
            (date(2026, 9, 25), 1, Decimal(5)),
            (date(2026, 9, 26), 1, Decimal(-2)),
        ]
        assert _days(connection, "POS_ACT_MTL") == [(date(2026, 9, 7), 1, Decimal("114.48"))]

    def test_the_days_add_up_to_the_month_exactly(self, connection: Connection) -> None:
        """Every driver of every month, the informative ones too, digit for digit."""
        reconciled = PostgresGoldReader(connection).daily_reconciliation(
            DateRange(AUGUST, date(2026, 9, 30))
        )
        assert len(reconciled) == len(_month(connection, AUGUST)) + len(
            _month(connection, SEPTEMBER)
        )
        assert [item.cuenta_stats for item in reconciled if not item.equal] == []
        assert {item.cuenta_stats for item in reconciled} >= {"LEADPROD", "UNITSHIP"}

    def test_a_changed_rule_still_adds_up(self, connection: Connection) -> None:
        """A second, negative rule and a new receipt: the days follow the month."""
        connection.execute(
            text(
                "INSERT INTO silver.instrucc (version, cuenta_stats, regla_n, campo_equs, signo) "
                "VALUES (1, 'OXPROD_KG', 2, 'PLACASLI', -1)"
            )
        )
        reconciled = PostgresGoldReader(connection).daily_reconciliation(
            DateRange.of_month(SEPTEMBER)
        )
        assert all(item.equal for item in reconciled)
        oxide = next(item for item in reconciled if item.cuenta_stats == "OXPROD_KG")
        assert oxide.daily_cantidad == 1500 - 840

    def test_a_day_uses_the_version_in_force_in_its_month(self, connection: Connection) -> None:
        """Version 2 from September: every September day uses it, August keeps version 1."""
        _add_version_2(connection)
        versions = {day.month: version for day, version, _ in _days(connection, "OXPROD_KG")}
        assert versions == {8: 1, 9: 2}

    def test_a_version_starting_mid_month_does_not_split_it(self, connection: Connection) -> None:
        """The version of a month is the one in force on its first day, for every day."""
        connection.execute(
            text("UPDATE silver.equs_version SET vigente_desde = '2026-09-15' WHERE version = 1")
        )
        assert [day for day, _, _ in _days(connection, "OXPROD_KG")] == []
        with pytest.raises(InvalidPeriodError, match="no version"):
            _series(connection, date(2026, 9, 16), date(2026, 9, 30))

    def test_the_series_keeps_the_battery_processes(self, connection: Connection) -> None:
        """No informative driver; each day's total; the whole month is checked."""
        series = _series(connection, SEPTEMBER, date(2026, 9, 30))

        assert series.procesos == ("OREC", "ENSAMBLE", "FORMACION", "DECORADO", "SHIPPING")
        assert {row.cuenta_stats for row in series.rows}.isdisjoint(
            {"LEADPROD", "FORM_PRD_SLI", "15BATPRD"}
        )
        factors = _factors(connection)
        by_day = {total.start: total for total in totals(series)}
        assert by_day[date(2026, 9, 5)].by_process["OREC"] == 1000 * factors["OXPROD_KG"] + (
            840 * factors["PLATEPRD_SLI"]
        )
        assert by_day[date(2026, 9, 26)].by_process["SHIPPING"] == -2 * factors["UNITSHIP"]
        month = connection.execute(
            text("SELECT equs_batt FROM gold.v_equs_batt_mes WHERE mes = :mes"),
            {"mes": SEPTEMBER},
        ).scalar_one()
        assert sum(total.total for total in by_day.values()) == month
        assert series.reconciled_months == (SEPTEMBER,)
        assert series.mismatches == ()

    def test_a_series_by_area_and_by_month(self, connection: Connection) -> None:
        """F&A is formation and decoration; by month it is v_equs_batt_mes."""
        series = _series(connection, AUGUST, date(2026, 9, 30), frequency="month", areas=["F&A"])

        assert series.procesos == ("FORMACION", "DECORADO")
        row = connection.execute(
            text("SELECT * FROM gold.v_equs_batt_mes WHERE mes = :mes"), {"mes": SEPTEMBER}
        ).one()
        september = totals(series)[1]
        assert september.by_process == {
            "FORMACION": row.equs_formacion,
            "DECORADO": row.equs_decorado,
        }
        assert totals(series)[0].total == 0
        assert series.reconciliation == ()


class TestMonthlyClose:
    """Closing a month freezes it; only a reclose changes it, and keeps what it replaced."""

    @pytest.fixture(scope="class")
    def closed(self, seeded: Engine) -> Engine:
        """September closed once."""
        close_month(SEPTEMBER, closed_by="first", lock_timeout=30, engine=seeded)
        return seeded

    @staticmethod
    def _frozen(engine: Engine, month: date = SEPTEMBER) -> dict[str, tuple[Decimal, Decimal]]:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT cuenta_stats, cantidad, equs FROM gold.resumen_equ WHERE mes = :m"),
                {"m": month},
            )
            return {row[0]: (row[1], row[2]) for row in rows}

    def test_closing_freezes_column_h(self, closed: Engine) -> None:
        """The month's v_resumen_equ rows, as they were."""
        with closed.connect() as connection:
            live = _month(connection, SEPTEMBER)
        assert self._frozen(closed) == live

    def test_a_closed_month_is_not_closed_again_by_accident(self, closed: Engine) -> None:
        """Without --reclose the close is refused and nothing changes."""
        with pytest.raises(CloseRefusedError, match="already closed"):
            close_month(SEPTEMBER, closed_by="second", lock_timeout=30, engine=closed)
        with closed.connect() as connection:
            closes = connection.execute(text("SELECT count(*) FROM gold.cierre_mes")).scalar_one()
        assert closes == 1

    def test_the_close_does_not_follow_silver(self, closed: Engine) -> None:
        """A changed receipt changes the live view, not the close; reports say so."""
        frozen = self._frozen(closed)
        with closed.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text(
                    "UPDATE silver.reporte_qad SET cambiar_cantidad_de_ubic = 1100 "
                    "WHERE numero_de_transaccion = 1"
                )
            )
            assert _month(connection, SEPTEMBER)["OXPROD_KG"][0] == 1600
            live = _valid(build_report(connection, "2026-09-01", "2026-09-30"))
            frozen_report = _valid(
                build_report(connection, "2026-09-01", "2026-09-30", source="closed")
            )
            transaction.rollback()
        assert self._frozen(closed) == frozen
        assert [w["codigo"] for w in live["advertencias"]] == ["difiere_del_cierre"]
        assert _subsection(frozen_report, "OXPROD_KG")["cantidad"] == 1500
        assert [w["codigo"] for w in frozen_report["advertencias"]] == ["poly_en_vivo"]

    def test_the_frozen_close_needs_whole_closed_months(self, closed: Engine) -> None:
        """Half of a closed month, or a month that is not closed, are refused."""
        with closed.connect() as connection:
            with pytest.raises(InvalidPeriodError) as partial:
                build_report(connection, "2026-09-01", "2026-09-15", source="closed")
            assert partial.value.details["partial_months"] == ["2026-09"]
            with pytest.raises(InvalidPeriodError) as unclosed:
                build_report(connection, "2026-08-01", "2026-09-30", source="closed")
            assert unclosed.value.details["months_not_closed"] == ["2026-08"]

    def test_column_h_of_a_closed_month(self, closed: Engine) -> None:
        """resumen-equ --source closed reads the close."""
        report, rows = resumen_equ(SEPTEMBER, source="closed", engine=closed)
        assert report["fuente"] == "closed"
        names = [row.nombre for row in rows]
        assert names.index("OXPROD_KG") < names.index("EQUS BATT") < names.index("EQUS POLY")

    def test_a_reclose_keeps_what_it_replaced(self, closed: Engine) -> None:
        """The earlier rows go to the history, under the reclose that replaced them."""
        before = self._frozen(closed)
        summary = close_month(
            SEPTEMBER, closed_by="second", reclose=True, lock_timeout=30, engine=closed
        )
        assert summary.frozen.reclosed
        assert summary.frozen.replaced == len(before)
        with closed.connect() as connection:
            history = connection.execute(
                text(
                    "SELECT cuenta_stats, cantidad, equs, closed_by "
                    "FROM gold.resumen_equ_historial WHERE cierre_id = :id"
                ),
                {"id": summary.frozen.cierre_id},
            ).all()
            closes = connection.execute(
                text("SELECT closed_by, recierre FROM gold.cierre_mes ORDER BY cierre_id")
            ).all()
        assert {row[0]: (row[1], row[2]) for row in history} == before
        assert {row[3] for row in history} == {"first"}
        assert [tuple(row) for row in closes][-1] == ("second", True)

    def test_a_blocking_check_stops_the_close_unless_accepted(self, closed: Engine) -> None:
        """August with a formed battery that has no paste."""
        with closed.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO silver.item_master (numero_articulo, linea_de_productos, "
                    "campo_equs) VALUES ('231999998', '3101', 'SLI-S')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO silver.reporte_qad (numero_de_transaccion, tipo_transaccion, "
                    "fecha_efectiva, numero_articulo, almac, cambiar_cantidad_de_ubic, "
                    "source_run_id) SELECT 200, 'RCT-WO', '2026-08-20', '231999998', '1515', 3, "
                    "source_run_id FROM silver.reporte_qad LIMIT 1"
                )
            )
        seen: list[Any] = []
        with pytest.raises(CloseRefusedError, match="blocking") as refused:
            close_month(AUGUST, closed_by="x", lock_timeout=30, on_quality=seen.append,
                        engine=closed)  # fmt: skip
        assert refused.value.details["checks"] == {"dq_sin_conversor": 1}
        assert seen
        assert self._frozen(closed, AUGUST) == {}

        summary = close_month(
            AUGUST, closed_by="x", accept_warnings=True, lock_timeout=30, engine=closed
        )
        assert summary.frozen.accepted_warnings
        assert summary.previous_month is None
        with closed.connect() as connection:
            recorded = connection.execute(
                text(
                    "SELECT advertencias_aceptadas, calidad_datos ->> 'dq_sin_conversor' "
                    "FROM gold.cierre_mes WHERE mes = :m"
                ),
                {"m": AUGUST},
            ).one()
        assert tuple(recorded) == (True, "1")

    def test_a_month_without_production_cannot_be_closed(self, closed: Engine) -> None:
        """Nothing to freeze."""
        with pytest.raises(CloseRefusedError, match="nothing to close"):
            close_month(date(2030, 1, 1), closed_by="x", lock_timeout=30, engine=closed)


def _python() -> str:
    return Path(sys.executable).as_posix()


def _environment(database_url: str) -> dict[str, str]:
    return os.environ | {"DATABASE_URL": database_url, "LOG_LEVEL": "WARNING"}


class TestRefreshAndLock:
    """refresh-gold: what it refreshes, what it never touches, and its lock."""

    def test_a_refresh_picks_up_a_changed_structure_and_leaves_the_close(
        self, seeded: Engine
    ) -> None:
        """td_form is stale until the refresh; the close never moves."""
        close_month(SEPTEMBER, closed_by="t", lock_timeout=30, engine=seeded)

        def paste() -> Decimal:
            with seeded.connect() as connection:
                return _month(connection, SEPTEMBER)["POS_ACT_MTL"][0]

        def frozen() -> Decimal:
            with seeded.connect() as connection:
                return connection.execute(
                    text(
                        "SELECT cantidad FROM gold.resumen_equ "
                        "WHERE mes = :m AND cuenta_stats = 'POS_ACT_MTL'"
                    ),
                    {"m": SEPTEMBER},
                ).scalar_one()

        change = (
            "UPDATE silver.bom_estructura SET cantidad_por = :q "
            "WHERE articulo_matriz = '216000006' AND articulo_componente = '211000003'"
        )
        with seeded.begin() as connection:
            connection.execute(text(change), {"q": Decimal("0.0700")})
        assert paste() == Decimal("114.48")

        outcome = refresh_gold(lock_timeout=30, engine=seeded)
        assert [step.name for step in outcome.steps] == [
            "take the refresh lock",
            "refresh silver.td_form",
            "refresh silver.td_deco",
            "analyze",
        ]
        assert paste() == 50 * 36 * Decimal("0.07")
        assert frozen() == Decimal("114.48")

        with seeded.begin() as connection:
            connection.execute(text(change), {"q": Decimal("0.0636")})
        refresh_gold(concurrently=False, lock_timeout=30, engine=seeded)
        assert paste() == Decimal("114.48")

    def test_a_held_lock_ends_a_refresh_after_its_timeout(
        self, seeded: Engine, database_url: str
    ) -> None:
        """In the service with LockTimeoutError, from the command line with exit 3."""
        with seeded.connect() as holder:
            holder.execute(text("SELECT pg_advisory_lock(7215590330)"))
            holder.commit()
            try:
                with pytest.raises(LockTimeoutError):
                    refresh_gold(lock_timeout=0.5, engine=seeded)
                finished = subprocess.run(  # noqa: S603 - this interpreter, fixed arguments
                    [_python(), "-m", "energy", "equs", "refresh-gold", "--lock-timeout", "1"],
                    capture_output=True,
                    text=True,
                    env=_environment(database_url),
                    timeout=120,
                    check=False,
                )
            finally:
                holder.execute(text("SELECT pg_advisory_unlock(7215590330)"))
                holder.commit()
        assert finished.returncode == 3, finished.stderr
        assert "did not finish in time" in finished.stderr

    @pytest.mark.usefixtures("seeded")
    def test_two_refreshes_started_together_both_finish(self, database_url: str) -> None:
        """The second waits for the first instead of running beside it."""
        command = [_python(), "-m", "energy", "equs", "refresh-gold", "--format", "json"]
        processes = [
            subprocess.Popen(  # noqa: S603 - this interpreter, fixed arguments
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=_environment(database_url),
            )
            for _ in range(2)
        ]
        results = [process.communicate(timeout=300) for process in processes]
        assert [process.returncode for process in processes] == [0, 0], results

    def test_a_concurrent_refresh_does_not_block_readers(self, seeded: Engine) -> None:
        """While td_form refreshes concurrently, column H can still be read."""
        with seeded.connect() as refresher, seeded.connect() as reader:
            refresher.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY silver.td_form"))
            # The refresh holds its lock until it commits; the reader must not wait.
            reader.execute(text("SET lock_timeout = '2s'"))
            rows = reader.execute(
                text("SELECT count(*) FROM gold.v_resumen_equ WHERE mes = :m"), {"m": SEPTEMBER}
            ).scalar_one()
            assert rows == len(SEPTEMBER_QUANTITIES)
            reader.rollback()
            refresher.commit()

            # A plain refresh does block them, which is why it is not the default.
            refresher.execute(text("REFRESH MATERIALIZED VIEW silver.td_form"))
            reader.execute(text("SET lock_timeout = '1s'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                reader.execute(text("SELECT count(*) FROM gold.v_resumen_equ"))
            reader.rollback()
            refresher.commit()

    def test_a_refresh_waits_rather_than_fails_within_its_timeout(self, seeded: Engine) -> None:
        """A lock released before the timeout is taken."""
        with seeded.connect() as holder:
            holder.execute(text("SELECT pg_advisory_lock(7215590330)"))
            holder.commit()
            timer = threading.Timer(
                1.0, lambda: (holder.execute(text("SELECT pg_advisory_unlock(7215590330)")),)
            )
            timer.start()
            try:
                outcome = refresh_gold(lock_timeout=30, engine=seeded)
            finally:
                timer.join()
            holder.commit()
        assert outcome.steps[0].seconds >= 0.5


# -- The make targets ---------------------------------------------------------------
MAKE = shutil.which("make")
needs_make = pytest.mark.skipif(MAKE is None, reason="make is not installed")


def _make(*arguments: str) -> subprocess.CompletedProcess[str]:
    assert MAKE is not None
    return subprocess.run(  # noqa: S603 - make, fixed targets
        [MAKE, "--no-print-directory", *arguments],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
        check=False,
    )


@needs_make
def test_make_help_lists_the_targets() -> None:
    """Each target with its one-line description."""
    listed = _make("help")
    assert listed.returncode == 0
    for target in ("refresh", "refresh-all", "resumen", "daily", "close", "report", "equs-load"):
        assert f"{target} " in listed.stdout


@needs_make
@pytest.mark.parametrize(
    ("arguments", "missing"),
    [
        (("resumen",), "MONTH"),
        (("close",), "MONTH"),
        (("report",), "START and END"),
        (("report", "START=2026-09-01"), "START and END"),
        (("report", "END=2026-09-30"), "START and END"),
        (("daily",), "MONTH or START and END"),
        (("daily", "START=2026-09-01"), "MONTH or START and END"),
    ],
)
def test_make_targets_refuse_a_missing_variable(arguments: tuple[str, ...], missing: str) -> None:
    """A clear message and a non-zero exit, before anything runs."""
    finished = _make(*arguments)
    assert finished.returncode != 0
    assert f"error: {missing} " in finished.stderr
