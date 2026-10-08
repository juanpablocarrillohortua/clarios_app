"""Build Silver from the real EQUS workbooks and check it against the business.

Layer: tests (integration)

These are the acceptance tests of the Silver layer. They land the workbooks of
``data/raw_excels`` in a database migrated to revision 0003, build Silver from
them, and compare the result with three things:

* the figures ``docs/EQUS_CONTEXT.md`` section 9 documents for these files;
* the workbooks themselves - the pivots the two conversion tables replace and
  the sheet that holds the final calculation - with every difference accounted
  for;
* a second build, which must change nothing.

The equivalent batteries of a month are computed here by a query over Silver
and the seeded rules. That query is the equation of the calculation written
once, for the tests; the Gold layer will own it.

The workbooks are not in the repository, so the module is skipped when they are
absent. Every test builds on one shared load and build.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.adapters.equs_silver_sql import SILVER_TABLES
from energy.adapters.local_storage import LocalObjectStorage
from energy.adapters.postgres_bronze_repo import PostgresBronzeLandingRepository
from energy.adapters.postgres_equs_silver import PostgresSilverBuildRepository
from energy.pipelines.flows.equs_bronze_load import ROUTES, find_workbook, load_all
from energy.services.bronze_loader import BronzeLoader

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from energy.domain.equs_silver import SilverBuildReport

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "raw_excels"

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


def _cents(value: Decimal) -> str:
    """Round to the cent the way the specification's figures were rounded.

    Half a cent goes up. July 2026 has one: shipping is 219,885 x 0.041 =
    9,015.285 exactly.
    """
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


# What each driver sums in a month: the rules of `instrucc` applied to the
# receipts (or to the invoice lines), times the conversion the driver asks for.
QUANTITIES = """
WITH recibo AS (
    SELECT r.*, a.linea_de_productos, date_trunc('month', r.fecha_efectiva)::date AS mes
    FROM silver.reporte_qad AS r
    JOIN silver.item_master AS a USING (numero_articulo)
),
produccion AS (
    SELECT r.mes, f.cuenta_stats,
           sum(i.signo * CASE f.fuente
                   WHEN 'MAT_ACTIVO' THEN r.cambiar_cantidad_de_ubic * coalesce(tf.pasta_pos, 0)
                   WHEN 'COMP_DECO' THEN r.cambiar_cantidad_de_ubic * coalesce(td.comp_deco, 0)
                   ELSE r.cambiar_cantidad_de_ubic
               END) AS cantidad
    FROM silver.factores_equ AS f
    JOIN silver.instrucc AS i USING (version, cuenta_stats)
    JOIN recibo AS r
      ON (i.linea_de_productos IS NULL OR r.linea_de_productos = i.linea_de_productos)
     AND (i.campo_equs IS NULL OR r.campo_equs = i.campo_equs)
     AND (i.campo_equs_excluir IS NULL OR r.campo_equs IS DISTINCT FROM i.campo_equs_excluir)
     AND (i.prefijo_linea IS NULL OR r.linea_produccion LIKE i.prefijo_linea || '%')
    LEFT JOIN silver.td_form AS tf ON tf.articulo_matriz = r.numero_articulo
    LEFT JOIN silver.td_deco AS td ON td.articulo_matriz = r.numero_articulo
    WHERE f.version = 1 AND f.fuente <> 'FACTURACION'
    GROUP BY r.mes, f.cuenta_stats
),
facturacion AS (
    SELECT date_trunc('month', u.fecha_efectiva)::date AS mes, f.cuenta_stats,
           sum(i.signo * u.cantidad_fac) AS cantidad
    FROM silver.factores_equ AS f
    JOIN silver.instrucc AS i USING (version, cuenta_stats)
    JOIN silver.unit_ship AS u ON true
    JOIN silver.item_master AS a
      ON a.numero_articulo = u.numero_articulo
     AND a.linea_de_productos = i.linea_de_productos
    WHERE f.version = 1 AND f.fuente = 'FACTURACION'
    GROUP BY 1, f.cuenta_stats
)
SELECT to_char(q.mes, 'YYYY-MM') AS mes, f.cuenta_stats, f.proceso, f.suma_eq,
       q.cantidad, q.cantidad * f.factor AS equs
FROM (SELECT * FROM produccion UNION ALL SELECT * FROM facturacion) AS q
JOIN silver.factores_equ AS f ON f.version = 1 AND f.cuenta_stats = q.cuenta_stats
"""

# EQUS_CONTEXT section 9.1: what each driver summed in September 2026.
SEPTEMBER_QUANTITIES = {
    "OXPROD_KG": Decimal("1162129"),
    "GRIDS_CAST": Decimal("9761100"),
    "GRIDS_NEG": Decimal("264006"),
    "PLATEPRD_SLI": Decimal("14859820"),
    "PLATEPRD_TRUCK": Decimal("790407"),
    "COSPRODU_SLI": Decimal("205185"),
    "COSPRODU_TRUCK": Decimal("16265"),
    "POS_ACT_MTL": Decimal("648763.763"),
    "COMP_DEC": Decimal("896534"),
    "UNITSHIP": Decimal("194609"),
    "FORM_PRD_SLI": Decimal("194846"),
    "FORM_PRD_TRUCK": Decimal("14816"),
    "LEADPROD": Decimal("1806206"),
}

# EQUS_CONTEXT section 9.2: equivalent batteries by month, target.
# (OREC, assembly, formation, decoration, shipping, EQUS BATT)
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
PROCESSES = ("OREC", "ENSAMBLE", "FORMACION", "DECORADO", "SHIPPING")


@dataclass(frozen=True)
class Built:
    """One shared load and build of the real workbooks."""

    engine: Engine
    repository: PostgresSilverBuildRepository
    report: SilverBuildReport


def _checksums(engine: Engine) -> dict[str, tuple[int, str]]:
    """Return the row count and a checksum of the contents of every Silver table."""
    result: dict[str, tuple[int, str]] = {}
    with engine.connect() as connection:
        for table in SILVER_TABLES:
            row = connection.execute(
                text(
                    # The names are the build's own list of Silver tables.
                    "SELECT count(*), md5(coalesce(string_agg(fila::text, '|' "  # noqa: S608
                    f"ORDER BY fila::text), '')) FROM silver.{table} AS fila"
                )
            ).one()
            result[table] = (row[0], row[1])
    return result


def _cells(filename: str, sheet: str) -> list[list[Any]]:
    """Read every row of a sheet of one of the source workbooks, as cell values."""
    path = find_workbook(DATA_DIR, filename)
    assert path is not None
    return [list(row) for row in CalamineWorkbookReader().open_workbook(path.read_bytes())
            .sheet(sheet).rows]  # fmt: skip


def _pivot(filename: str, sheet: str, header_row: int, value_column: int) -> dict[str, Decimal]:
    """Read a two-column pivot: item code in column A, a number further right.

    Args:
        filename: the workbook.
        sheet: the pivot sheet.
        header_row: the Excel row of the pivot's header; data starts below it.
        value_column: 0-based index of the column holding the value.

    Returns:
        Item code to value. Subtotal rows, whose first cell is not a code, are
        left out.
    """
    values: dict[str, Decimal] = {}
    for number, row in enumerate(_cells(filename, sheet), start=1):
        if number <= header_row or not row or row[0] in (None, ""):
            continue
        item = str(row[0]).strip()
        value = row[value_column] if len(row) > value_column else None
        if item[:1].isdigit() and isinstance(value, int | float):
            values[item] = Decimal(repr(float(value)))
    return values


@pytest.fixture(scope="class")
def built(silver_engine: Engine, tmp_path_factory: pytest.TempPathFactory) -> Built:
    """Land the six workbooks and build Silver once for the whole class."""
    loader = BronzeLoader(
        reader=CalamineWorkbookReader(),
        storage=LocalObjectStorage(tmp_path_factory.mktemp("bronze_archive")),
        repository=PostgresBronzeLandingRepository(silver_engine),
        bucket="bronze-raw",
    )
    summary = load_all(loader, DATA_DIR, routes=EXACT_ROUTES)
    assert not [outcome.error for outcome in summary.outcomes if outcome.error]

    repository = PostgresSilverBuildRepository(silver_engine)
    return Built(silver_engine, repository, repository.build())


class TestSilverBuild:
    """Silver built from the six workbooks, checked against the documented figures."""

    @staticmethod
    def _rows(built: Built, statement: str, **parameters: Any) -> list[Any]:
        with built.engine.connect() as connection:
            return list(connection.execute(text(statement), parameters).all())

    def test_every_movement_is_in_silver(self, built: Built) -> None:
        """138,773 receipts and 14,458 invoice lines; none dropped, none without quantity."""
        rows = built.report.rows

        assert rows["reporte_qad"] == 138_773
        assert rows["unit_ship"] == 14_458
        assert (built.report.receipts_left_out, built.report.invoice_lines_left_out) == (0, 0)

        checks = self._rows(
            built,
            "SELECT count(*) FILTER (WHERE campo_equs = 'N/A') AS not_applicable, "
            "count(*) FILTER (WHERE cambiar_cantidad_de_ubic IS NULL) AS no_quantity, "
            "count(*) FILTER (WHERE cambiar_cantidad_de_ubic < 0) AS reversals, "
            "count(*) FILTER (WHERE linea_produccion IS NULL) AS no_line, "
            "count(DISTINCT tipo_transaccion) AS kinds FROM silver.reporte_qad",
        )[0]
        # "N/A" is a value: exactly as many as the export holds.
        assert checks.not_applicable == 6_582
        assert checks.no_quantity == 0
        # Reversals and the receipts QAD records without a line are kept.
        assert checks.reversals == 596
        assert checks.no_line == 2_518
        assert checks.kinds == 1

        credit_notes = self._rows(
            built,
            "SELECT count(*) AS lines, sum(cantidad_fac) AS units FROM silver.unit_ship "
            "WHERE cantidad_fac < 0",
        )[0]
        assert (credit_notes.lines, credit_notes.units) == (203, Decimal("-49373"))

    def test_the_conversion_tables_have_the_documented_size(self, built: Built) -> None:
        """One row per formed battery with paste, one per battery with pieces."""
        assert built.report.rows["td_form"] == 1_248
        assert built.report.rows["td_deco"] == 4_889

    def test_paste_per_formed_battery_spot_checks(self, built: Built) -> None:
        """EQUS_CONTEXT section 9.3, first table."""
        rows = self._rows(
            built,
            "SELECT articulo_matriz, pasta_pos, pasta_neg FROM silver.td_form "
            "WHERE articulo_matriz IN ('231000001', '231000902', '232000008')",
        )
        paste = {row.articulo_matriz: (row.pasta_pos, row.pasta_neg) for row in rows}

        # 36 x 0.0636 and 30 x 0.066.
        assert paste["231000001"] == (Decimal("2.2896"), Decimal("1.98"))
        # The workbook shows 4.3026: its plate is listed twice in PLACAS.
        assert paste["231000902"] == (Decimal("2.1513"), Decimal("2.0472"))
        # 60 x 0.058, the fictitious plate of imported batteries.
        assert paste["232000008"] == (Decimal("3.48"), Decimal("0"))

    def test_decoration_pieces_spot_checks(self, built: Built) -> None:
        """EQUS_CONTEXT section 9.3, second table."""
        rows = self._rows(
            built,
            "SELECT articulo_matriz, comp_deco FROM silver.td_deco "
            "WHERE articulo_matriz IN ('311000006', '311005893', '321000729')",
        )
        pieces = {row.articulo_matriz: row.comp_deco for row in rows}

        assert pieces == {
            "311000006": Decimal("4"),
            # The workbook shows 4: its rows are repeated in sheet Datos.
            "311005893": Decimal("2"),
            # The workbook shows 4: it misses the two terminal protectors.
            "321000729": Decimal("6"),
        }

    def test_repeated_structure_links_are_dropped(self, built: Built) -> None:
        """Rule 3, per sheet, and what the 790 of the specification really is.

        The specification expects 7, 13, 11 and 790. The first three are repeats
        by business key and are met. Sheet Datos has 70 of those. Its 790 is the
        number of rows that repeat another one cell for cell, and 740 of them
        are not links at all: rows 28,520 to 29,260 hold a "1" and nothing else.
        Those 741 are left out as incomplete; the conversion tables are the
        same either way.
        """
        sheets = {sheet.sheet: sheet for sheet in built.report.structure}

        assert built.report.duplicates_dropped == {
            "BAT ENS": 13,
            "BAT FOR": 11,
            "BAT TERM": 0,
            "Datos": 70,
            "PASTA": 0,
            "PLACAS": 7,
        }
        assert sheets["Datos"].incomplete_rows == 741
        exact_repeats = self._rows(
            built,
            "SELECT count(*) - count(DISTINCT content_hash) AS repeats, "
            "count(*) FILTER (WHERE NOT payload ? 'Artículo Componente') AS junk "
            "FROM bronze.raw_product_struc WHERE sheet_name = 'Datos'",
        )[0]
        assert (exact_repeats.repeats, exact_repeats.junk) == (790, 741)

        # A header row pasted into each of two sheets, and a stray cell.
        assert (sheets["PLACAS"].header_rows, sheets["PLACAS"].incomplete_rows) == (1, 1)
        assert (sheets["BAT FOR"].header_rows, sheets["BAT FOR"].reread_rows) == (1, 453)
        # The link from a decorated battery to its formed battery, which both
        # workbooks deliver, is kept once.
        assert sheets["BAT TERM"].in_another_sheet == 1_777
        assert built.report.rows["bom_estructura"] == sum(sheet.links for sheet in sheets.values())

    def test_building_twice_gives_identical_tables(self, built: Built) -> None:
        """Idempotent: row counts and checksums of every table and view."""
        before = _checksums(built.engine)

        again = built.repository.build()

        assert _checksums(built.engine) == before
        assert again.rows == built.report.rows
        assert again.structure == built.report.structure

    def test_september_2026_quantities(self, built: Built) -> None:
        """EQUS_CONTEXT section 9.1: what every driver sums in the month."""
        rows = self._rows(built, QUANTITIES + " WHERE to_char(q.mes, 'YYYY-MM') = '2026-09'")
        quantities = {row.cuenta_stats: row.cantidad for row in rows}

        assert {
            name: quantities[name].quantize(Decimal("0.001")) for name in SEPTEMBER_QUANTITIES
        } == (SEPTEMBER_QUANTITIES)
        # No positive strip was produced; the carton driver has no rule.
        assert quantities.get("GRIDS_REG_POS", Decimal(0)) == 0
        assert "CARTON_BATT" not in quantities

    def test_equivalent_batteries_of_every_month(self, built: Built) -> None:
        """EQUS_CONTEXT section 9.2, to the cent, and the fiscal year."""
        rows = self._rows(built, QUANTITIES)
        months: dict[str, dict[str, Decimal]] = {}
        for row in rows:
            if row.suma_eq:
                month = months.setdefault(row.mes, dict.fromkeys(PROCESSES, Decimal(0)))
                month[row.proceso] += row.equs

        computed = {
            month: (
                *(_cents(values[process]) for process in PROCESSES),
                _cents(sum(values.values(), Decimal(0))),
            )
            for month, values in months.items()
        }
        assert computed == EQ_BY_MONTH

        fiscal_year = sum(sum(values.values()) for values in months.values())
        assert round(fiscal_year) == 2_310_724
        formation = sum(values["FORMACION"] for values in months.values())
        assert round(formation) == 296_516

    def test_the_report_of_the_real_data(self, built: Built) -> None:
        """What the build says about these files."""
        report = built.report

        # Every formed and decorated battery produced has its conversion.
        assert report.formed_without_paste == ()
        assert report.decorated_without_pieces == ()
        assert report.items_without_description == ()

        without_plant = {line.line: line.receipts for line in report.lines_without_plant}
        assert len(without_plant) == 14
        assert {line for line, receipts in without_plant.items() if receipts} == {
            "FADECO05",
            "POLINY05",
            "MAQBUJEO",
        }
        # The pellet has a category the factor table does not list.
        assert [(item.item, item.category) for item in report.poly_factor_mismatches] == [
            ("157000012", "PELLET")
        ]
        assert report.poly_repeated_items == ("111000005",)
        assert len(report.na_with_poly_category) == 44
        # One formed battery has two assembled batteries: see the pivot test.
        assert [
            (item.item, item.assembled, item.quantity)
            for item in report.formed_with_several_assembled
        ] == [("231001483", 2, Decimal("1008"))]
        assert report.placeholders == {
            "linea_de_productos": 3,
            "lineas_produccion": 0,
            "item_master": 0,
            "vendido_a": 0,
        }
        assert report.concurrent_refresh

    def test_catalogues_are_conformed(self, built: Built) -> None:
        """Lines under one name, countries in one spelling, both spellings of N/A."""
        lines = self._rows(
            built,
            "SELECT linea, planta FROM silver.lineas_produccion "
            "WHERE linea IN ('OEMPSOV1', 'FAFORM02') ORDER BY 1",
        )
        assert [tuple(row) for row in lines] == [("FAFORM02", "F&A"), ("OEMPSOV1", "OREC")]
        assert built.report.rows["lineas_produccion"] == 64
        assert not self._rows(
            built,
            "SELECT 1 FROM silver.reporte_qad WHERE linea_produccion <> upper(linea_produccion)",
        )

        countries = {
            row[0] for row in self._rows(built, "SELECT DISTINCT pais FROM silver.vendido_a")
        }
        assert "Colombia" in countries
        assert "COLOMBIA" not in countries

        categories = {
            row[0] for row in self._rows(built, "SELECT campo_equs FROM silver.campo_equs")
        }
        assert {"N/A", "NA", "OXIDO", "TRUCK-B", "PLACA HD"} <= categories
        assert {row[0] for row in self._rows(built, "SELECT almac FROM silver.almacen")} == {
            "1010",
            "1515",
            "1520",
            "1521",
        }

    def test_polarity_of_pastes_and_plates(self, built: Built) -> None:
        """Every paste and every plate of the structure has one; nothing else does."""
        rows = self._rows(
            built,
            "SELECT numero_articulo, polaridad FROM silver.item_master "
            "WHERE numero_articulo IN ('211000003', '21100000A', '21500000A', '216000006')",
        )
        assert {row.numero_articulo: row.polaridad for row in rows} == {
            "211000003": "+",
            "21100000A": "+",
            "21500000A": "+",
            "216000006": "+",
        }
        unsigned_plates = self._rows(
            built,
            "SELECT DISTINCT b.articulo_matriz FROM silver.bom_estructura AS b "
            "JOIN silver.item_master AS m ON m.numero_articulo = b.articulo_matriz "
            "WHERE b.source_sheet = 'PLACAS' AND m.polaridad IS NULL",
        )
        assert unsigned_plates == []
        others = self._rows(
            built,
            "SELECT count(*) FROM silver.item_master WHERE polaridad IS NOT NULL "
            "AND descripcion NOT LIKE 'PASTA%' AND descripcion NOT LIKE 'PLACA%'",
        )
        assert others[0][0] == 0

    def test_lineage_points_at_the_bronze_run(self, built: Built) -> None:
        """Every structure link and movement names a completed run and its sheet."""
        rows = self._rows(
            built,
            "SELECT b.source_sheet, count(DISTINCT b.source_run_id) AS runs, "
            "bool_and(r.state = 'completed' AND r.source_key = 'qad_product_struc') AS completed "
            "FROM silver.bom_estructura AS b "
            "JOIN bronze.ingestion_runs AS r ON r.run_id = b.source_run_id GROUP BY 1",
        )
        assert {row.source_sheet: (row.runs, row.completed) for row in rows} == {
            "PASTA": (1, True),
            "PLACAS": (1, True),
            "BAT ENS": (1, True),
            "BAT FOR": (1, True),
            "BAT TERM": (1, True),
            "Datos": (1, True),
        }
        movements = self._rows(
            built,
            "SELECT (SELECT count(DISTINCT source_run_id) FROM silver.reporte_qad), "
            "(SELECT count(DISTINCT source_run_id) FROM silver.unit_ship)",
        )[0]
        assert tuple(movements) == (1, 1)

    # -- Against the workbooks themselves --------------------------------------

    def test_the_seeded_factors_are_the_ones_of_the_workbook(self, built: Built) -> None:
        """Column G of sheet RESUMEN EQU, driver by driver."""
        resumen = _cells("EQUS BATT.xlsx", "RESUMEN EQU")
        workbook = {
            str(row[0]): Decimal(repr(float(row[6])))
            for row in resumen[1:18]
            if row and row[0] and len(row) > 6 and isinstance(row[6], int | float)
        }
        rows = self._rows(built, "SELECT cuenta_stats, factor FROM silver.factores_equ")
        seeded = {row.cuenta_stats: row.factor for row in rows}

        assert len(workbook) == 12
        for driver, factor in workbook.items():
            assert float(seeded[driver]) == pytest.approx(float(factor), rel=1e-12), driver
        # The four drivers the workbook gives no factor are the informative ones.
        assert {driver for driver in seeded if driver not in workbook} == {
            "15BATPRD",
            "FORM_PRD_SLI",
            "FORM_PRD_TRUCK",
            "LEADPROD",
        }

    def test_september_quantities_against_the_workbook(self, built: Built) -> None:
        """Column D of sheet RESUMEN EQU: equal, except where the workbook is wrong.

        The workbook holds September 2026. Three drivers differ, each for a
        known reason; every other quantity is the same number.
        """
        resumen = _cells("EQUS BATT.xlsx", "RESUMEN EQU")
        workbook = {
            str(row[0]): Decimal(repr(float(row[3])))
            for row in resumen[1:18]
            if row and row[0] and len(row) > 3 and isinstance(row[3], int | float)
        }
        rows = self._rows(built, QUANTITIES + " WHERE to_char(q.mes, 'YYYY-MM') = '2026-09'")
        silver = {row.cuenta_stats: row.cantidad for row in rows}

        different = {
            driver
            for driver, quantity in workbook.items()
            if abs(silver.get(driver, Decimal(0)) - quantity) > Decimal("0.0005")
        }
        assert different == {"POS_ACT_MTL", "COMP_DEC", "15BATPRD"}
        # Lower: plates listed twice in PLACAS no longer double the paste.
        assert workbook["POS_ACT_MTL"].quantize(Decimal("0.01")) == Decimal("654835.08")
        assert silver["POS_ACT_MTL"] < workbook["POS_ACT_MTL"]
        # Higher: the terminal protectors of imported batteries are counted.
        assert silver["COMP_DEC"] - workbook["COMP_DEC"] == 970
        # The specification gives this informative driver no rule.
        assert workbook["15BATPRD"] == 209_662
        assert "15BATPRD" not in silver

    def test_paste_per_formed_battery_against_the_pivot(self, built: Built) -> None:
        """Pivot TD_FORM of BOM BATERIAS FORMADAS, item by item.

        1,197 of the 1,248 formed batteries have exactly the workbook's value.
        Fifty are a half or a quarter of it: a link on their way down to the
        paste is repeated in PLACAS, BAT ENS or BAT FOR, and the workbook counts
        it twice. One is double: 231001483
        has two assembled batteries, the link to the old one closed in January
        2026, and the workbook counts only the one it marks.
        """
        workbook = _pivot("BOM BATERIAS FORMADAS.xlsx", "TD_FORM", 3, 5)
        rows = self._rows(built, "SELECT articulo_matriz, pasta_pos FROM silver.td_form")
        silver = {row.articulo_matriz: row.pasta_pos for row in rows}
        tolerance = Decimal("0.000001")

        assert set(workbook) == set(silver)
        higher = {i for i in silver if workbook[i] - silver[i] > tolerance}
        lower = {i for i in silver if silver[i] - workbook[i] > tolerance}

        assert len(silver) - len(higher) - len(lower) == 1_197
        assert len(higher) == 50
        assert {round(workbook[i] / silver[i]) for i in higher} == {2, 4}
        assert all(abs(workbook[i] / silver[i] - round(workbook[i] / silver[i])) < tolerance
                   for i in higher)  # fmt: skip
        # Each of the fifty has a link repeated somewhere on its way down: in its
        # own structure (BAT FOR), in that of its assembled battery (BAT ENS), or
        # in that of one of its plates (PLACAS).
        explained = self._rows(
            built,
            "WITH repetido AS ("
            " SELECT DISTINCT sheet_name, payload ->> 'Artículo Matriz' AS matriz, "
            "  payload ->> 'Artículo Componente' AS componente "
            " FROM bronze.raw_product_struc "
            " WHERE sheet_name IN ('BAT FOR', 'BAT ENS', 'PLACAS') "
            " GROUP BY sheet_name, payload ->> 'Artículo Matriz', "
            "  payload ->> 'Artículo Componente', payload ->> 'Cantidad Por', "
            "  payload ->> 'Inicio Efectiva', payload ->> 'Fin Efectivo' "
            " HAVING count(*) > 1) "
            "SELECT DISTINCT formada.articulo_matriz FROM silver.bom_estructura AS formada "
            "JOIN silver.bom_estructura AS ensamblada "
            "  ON ensamblada.articulo_matriz = formada.articulo_componente "
            "WHERE EXISTS (SELECT 1 FROM repetido WHERE sheet_name = 'BAT FOR' "
            "   AND matriz = formada.articulo_matriz "
            "   AND componente = formada.articulo_componente) "
            "  OR EXISTS (SELECT 1 FROM repetido WHERE sheet_name = 'BAT ENS' "
            "   AND matriz = ensamblada.articulo_matriz "
            "   AND componente = ensamblada.articulo_componente) "
            "  OR EXISTS (SELECT 1 FROM repetido WHERE sheet_name = 'PLACAS' "
            "   AND matriz = ensamblada.articulo_componente)",
        )
        assert higher <= {row.articulo_matriz for row in explained}

        assert lower == {"231001483"}
        assert silver["231001483"] == 2 * workbook["231001483"].quantize(Decimal("0.0001"))
        assert lower == {item.item for item in built.report.formed_with_several_assembled}

    def test_decoration_pieces_against_the_pivot(self, built: Built) -> None:
        """Pivot TD of ESTRUCTURA DECORADO, item by item.

        4,873 of the 4,889 batteries have exactly the workbook's value. Two are
        half of it, because their rows are repeated in sheet Datos. Fourteen
        imported batteries have two pieces more: the terminal protectors the
        workbook's formula stops counting in its last rows.
        """
        workbook = _pivot("ESTRUCTURA DECORADO.xlsx", "TD", 5, 2)
        rows = self._rows(
            built,
            "SELECT t.articulo_matriz, t.comp_deco, m.linea_de_productos FROM silver.td_deco AS t "
            "JOIN silver.item_master AS m ON m.numero_articulo = t.articulo_matriz",
        )
        silver = {row.articulo_matriz: row.comp_deco for row in rows}
        product_line = {row.articulo_matriz: row.linea_de_productos for row in rows}

        assert set(workbook) == set(silver)
        higher = {i for i in silver if workbook[i] > silver[i]}
        lower = {i for i in silver if workbook[i] < silver[i]}

        assert len(silver) - len(higher) - len(lower) == 4_873
        assert higher == {"311005893", "311006010"}
        assert all(workbook[i] == 2 * silver[i] for i in higher)
        assert len(lower) == 14
        assert {silver[i] - workbook[i] for i in lower} == {Decimal("2")}
        assert {product_line[i] for i in lower} == {"3104"}
