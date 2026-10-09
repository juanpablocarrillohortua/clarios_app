"""The rules of the Bronze to Silver build, on a handful of rows written here.

Layer: tests (integration)

The acceptance tests in ``test_equs_silver_build.py`` check the build against
the real workbooks, which only exist on the machines of the people who work
with them. These tests state each rule on its own instead: they write a few
Bronze rows that reproduce one defect or one case of the real exports each -
a header pasted among the data, a link delivered twice, a receipt landed by two
runs - build Silver, and check what came out. They need no file from the
business, so they run everywhere.

The payload keys are the header texts of the real exports, accents included.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

from energy.adapters.equs_silver_sql import SILVER_TABLES, SIN_DESCRIPCION
from energy.adapters.postgres_equs_silver import PostgresSilverBuildRepository
from energy.domain.exceptions import PipelineError
from energy.domain.landing import canonical_json, sha256_hex
from energy.ports.equs_silver import SilverBuildRepository

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine

    from energy.domain.equs_silver import SilverBuildReport

pytestmark = pytest.mark.integration

FIRST = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


def _land(
    connection: Connection,
    table: str,
    source_key: str,
    sheet: str,
    rows: list[dict[str, Any]],
    *,
    minutes: int = 0,
    state: str = "completed",
    dataset_key: str | None = None,
) -> Any:
    """Write one Bronze run and its rows, as the loader would have.

    Args:
        connection: an open connection.
        table: the landing table.
        source_key: the source key of the run.
        sheet: the worksheet name.
        rows: the payloads, in sheet order; the first is Excel row 2.
        minutes: when the run happened, after ``FIRST``. A later run is newer.
        state: the state the run ended in.
        dataset_key: the catalogue name, for hand-maintained sheets.

    Returns:
        The id of the run.
    """
    moment = FIRST + timedelta(minutes=minutes)
    run_id = connection.execute(
        text(
            "INSERT INTO bronze.ingestion_runs "
            "(flow_name, source_key, source_system, plant_code, state, started_at, finished_at) "
            "VALUES ('rules', :source_key, 'qad', '1515', :state, :moment, :moment) "
            "RETURNING run_id"
        ),
        {"source_key": source_key, "state": state, "moment": moment},
    ).scalar_one()

    extra_column = "dataset_key, " if dataset_key else ""
    extra_value = ":dataset_key, " if dataset_key else ""
    statement = text(
        # The table name is one of the landing tables named in this module.
        f"INSERT INTO bronze.{table} "  # noqa: S608
        f"({extra_column}ingestion_run_id, ingested_at, source_system, source_uri, sheet_name, "
        " source_row, content_hash, plant_code, payload) "
        f"VALUES ({extra_value}:run_id, :moment, 'qad', 'test://rules', :sheet, :source_row, "
        " :content_hash, '1515', CAST(:payload AS jsonb))"
    )
    for source_row, payload in enumerate(rows, start=2):
        cells = {key: str(value) for key, value in payload.items() if value is not None}
        parameters = {
            "run_id": run_id,
            "moment": moment,
            "sheet": sheet,
            "source_row": source_row,
            "content_hash": sha256_hex(canonical_json(cells).encode()),
            "payload": json.dumps(cells, ensure_ascii=False),
        }
        if dataset_key:
            parameters["dataset_key"] = dataset_key
        connection.execute(statement, parameters)
    return run_id


def _link(
    matriz: str,
    descripcion: str,
    componente: str,
    descripcion_componente: str,
    cantidad: str,
    **more: Any,
) -> dict[str, Any]:
    """Build one product-structure row with the headers every sheet shares."""
    return {
        "Artículo Matriz": matriz,
        "Descripción": descripcion,
        "Artículo Componente": componente,
        "Descripción Componente": descripcion_componente,
        "Cantidad Por": cantidad,
        "Inicio Efectiva": "2020-01-01",
        **more,
    }


def _receipt(
    number: int,
    item: str,
    quantity: str,
    day: str,
    *,
    line: str | None = "ENSLIN01",
    campo: str = "SLI-S",
    price: str = "100",
    description: str | None = "BE 75 6*5",
    product_line: str = "2201",
    warehouse: str = "1515",
) -> dict[str, Any]:
    """Build one production receipt as the transactions export delivers it."""
    return {
        "Número de transacción": number,
        "Tipo Transacción": "RCT-WO",
        "Fecha Efectiva": day,
        "Fecha": day,
        "Número Artículo": item,
        "Descripción de Artículo": description,
        "Almac": warehouse,
        "Línea de productos": product_line,
        "Descripción": f"LINEA {product_line}",
        "Línea Producción": line,
        "Cambiar cantidad de ubic": quantity,
        "Unidad de medida": "UN",
        "Precio": price,
        "Costo Total": "0",
        "Campo Equs": campo,
        "Peso Neto": "17.5",
    }


def _invoice(number: str, order: str, quantity: str, customer: str, country: str) -> dict[str, Any]:
    """Build one invoice line as the invoices export delivers it."""
    return {
        "Núm Fact": number,
        "Orden": order,
        "Línea": 1,
        "Canal": "Venta",
        "Fecha de la Factura": "2026-01-20",
        "Fecha Efectiva": "2026-01-21T00:00:00",
        "Vendido-A": customer,
        "Ordenar Nombre": f"CLIENTE {customer}",
        "País": country,
        "Tipo": "NAL",
        "Número Artículo": "311000006",
        "Descripcion": "BT 75 MAC",
        "UM": "UN",
        "Línea de productos": "3103",
        "Descripción Línea Producto": "BATERIAS DECORADAS",
        "Cantidad fac": quantity,
        "Moneda": "COP",
        "Campo Equs": "SLI-S",
    }


# The header row someone pasted into sheet BAT FOR, as Bronze keyed it: each
# cell is filed under the sheet's own header for its column, and holds the
# header text of the export the block below came from. That export has the type
# and the group the other way round, and the unit where the sheet has the
# second description.
PASTED_HEADER = {
    "Artículo Matriz": "Artículo Matriz",
    "Descripción": "DESCRIPCION_DE_ARTICULO",
    "Descripción #2": "Unidad de medida",
    "Tipo de artículo": "Grupo",
    "Grpo": "Tipo de artículo",
    "Línea de productos": "Línea de productos",
    "Artículo Componente": "Artículo Componente",
    "Descripción Componente": "Descripción Componente",
    "Cantidad Por": "Cantidad Por",
    "Inicio Efectiva": "Inicio Efectiva",
}


def _land_everything(connection: Connection) -> dict[str, Any]:
    """Write the Bronze rows every test of the class reads. Returns the run ids."""
    runs: dict[str, Any] = {}

    # -- Product structures ----------------------------------------------------
    _land(
        connection,
        "raw_product_struc",
        "qad_product_struc",
        "PASTA",
        [
            _link(
                "211000003",
                "PASTA AUTOMOTRIZ POSITIVA",
                "210000001",
                "OXIDO DE PLOMO",
                "0.8",
                **{"Tipo de artículo": "POSITVO", "Línea de productos": "2101"},
            ),
            _link(
                "211000004",
                "PASTA AUTOMOTRIZ CURADA",
                "210000001",
                "OXIDO DE PLOMO",
                "0.8",
                **{"Tipo de artículo": "NEGATIVO", "Línea de productos": "2101"},
            ),
            # No type the rule knows: its description decides.
            _link(
                "211000009",
                "PASTA ESPECIAL NEG",
                "210000001",
                "OXIDO DE PLOMO",
                "0.7",
                **{"Tipo de artículo": "N/A"},
            ),
        ],
    )

    # An older load of PLACAS that a newer one replaces: none of it may be used.
    _land(
        connection,
        "raw_product_struc",
        "qad_product_struc",
        "PLACAS",
        [_link("216000006", "PLACA LN (+) SOV", "211000003", "PASTA AUTOMOTRIZ POSITIVA", "0.9")],
        minutes=-60,
    )
    placas = [
        _link(
            "216000006",
            "PLACA LN (+) SOV",
            "211000003",
            "PASTA AUTOMOTRIZ POSITIVA",
            "0.0636",
            COSTO_STD="10",
        ),
        # Not paste, so not part of the paste of the plate.
        _link("216000006", "PLACA LN (+) SOV", "213000001", "REJILLA SOV", "1"),
        _link("216000007", "PLACA LN (-) SOV", "211000004", "PASTA AUTOMOTRIZ CURADA", "0.066"),
        # The same link from another export: another cost, still the same link.
        _link(
            "216000006",
            "PLACA LN (+) SOV",
            "211000003",
            "PASTA AUTOMOTRIZ POSITIVA",
            "0.0636",
            COSTO_STD="12",
        ),
        # A plate that says its polarity only in its type.
        _link(
            "216000008",
            "PLACA LN SOV",
            "211000009",
            "PASTA ESPECIAL NEG",
            "0.05",
            **{"Tipo de artículo": "NEGATIVO"},
        ),
        _link("21500000A", "PLACA PARA BAT IMPO", "21100000A", "PASTA BAT IMPO", "0.058"),
        # A stray cell, and a header row pasted at the end of the sheet.
        {"Estado": "215000002"},
        {"Artículo Matriz": "Artículo Matriz", "Artículo Componente": "Descripción Componente"},
    ]
    runs["placas"] = _land(connection, "raw_product_struc", "qad_product_struc", "PLACAS", placas)
    # A load that failed after the good one: it landed nothing that counts.
    _land(
        connection,
        "raw_product_struc",
        "qad_product_struc",
        "PLACAS",
        [_link("216000006", "PLACA LN (+) SOV", "211000003", "PASTA", "5")],
        minutes=30,
        state="failed",
    )

    positive = _link(
        "221000001",
        "BE 75 6*5",
        "216000006",
        "PLACA LN (+) SOV",
        "36",
        **{"Línea de productos": "2201", "Descripción #2": "ENS"},
    )
    _land(
        connection,
        "raw_product_struc",
        "qad_product_struc",
        "BAT ENS",
        [
            positive,
            _link("221000001", "BE 75 6*5", "216000007", "PLACA LN (-) SOV", "30"),
            # Pasted twice, cell for cell.
            positive,
            # An imported assembled battery: no product line, a fictitious plate.
            _link("222000003", "BE IMPO", "21500000A", "PLACA PARA BAT IMPO", "60"),
        ],
    )

    _land(
        connection,
        "raw_product_struc",
        "qad_product_struc",
        "BAT FOR",
        [
            _link(
                "231000001",
                "BF 75 6*5",
                "221000001",
                "BE 75 6*5",
                "1",
                CALCULO="SI",
                **{
                    "Línea de productos": "3101",
                    "Descripción #2": "FORM",
                    "Tipo de artículo": "SLI-T",
                    "Grpo": "7511AX",
                },
            ),
            # A component that is not the assembled battery carries no CALCULO.
            _link(
                "231000001",
                "BF 75 6*5",
                "130000001",
                "ACIDO",
                "2.5",
                **{"Línea de productos": "3101"},
            ),
            # Its assembled battery is not in BAT ENS, so the workbook says NO.
            _link(
                "231000002",
                "BF SIN ENSAMBLADA",
                "221000099",
                "BE DESCONOCIDA",
                "1",
                CALCULO="NO",
                **{"Línea de productos": "3101"},
            ),
            _link(
                "232000008",
                "BF IMPO",
                "222000003",
                "BE IMPO",
                "1",
                CALCULO="SI",
                **{"Línea de productos": "3101"},
            ),
            # Two assembled batteries: the link to the old one was closed, and
            # only the new one is marked. A blank CALCULO counts as SI, as specified.
            _link(
                "231000950",
                "BF DOS ENSAMBLADAS",
                "221000001",
                "BE 75 6*5",
                "1",
                CALCULO="SI",
                **{"Línea de productos": "3101"},
            ),
            _link(
                "231000950",
                "BF DOS ENSAMBLADAS",
                "222000003",
                "BE IMPO",
                "1",
                **{"Línea de productos": "3101", "Fin Efectivo": "2026-01-07"},
            ),
            PASTED_HEADER,
            # Below the pasted header: type and group swapped, unit in column C.
            _link(
                "231000900",
                "BF 24 6*5",
                "221000001",
                "BE 75 6*5",
                "1",
                CALCULO="SI",
                **{
                    "Línea de productos": "3101",
                    "Descripción #2": "UN",
                    "Tipo de artículo": "24-12 LTH",
                    "Grpo": "SLI-PMV",
                },
            ),
        ],
    )

    decorated = {"Línea de productos": "3103", "Unidad de medida": "UN"}
    label = _link(
        "311000006",
        "BT 75 MAC",
        "171000001",
        "ETIQUETA FRONTAL MAC",
        "2",
        **decorated,
        **{"LINEA DE PRODUCTO": "1401"},
    )
    formed = _link(
        "311000006",
        "BT 75 MAC",
        "231000001",
        "BF 75 6*5",
        "1",
        **decorated,
        **{"LINEA DE PRODUCTO": "3101"},
    )
    runs["datos"] = _land(
        connection,
        "raw_product_struc",
        "qad_product_struc",
        "Datos",
        [
            label,
            _link(
                "311000006",
                "BT 75 MAC",
                "142000010",
                "TAPA TERMINAL ROJA",
                "2",
                **decorated,
                **{"LINEA DE PRODUCTO": "1202"},
            ),
            # A handle of line 1202 is never a decoration element, whatever the
            # catalogue says.
            _link(
                "311000006",
                "BT 75 MAC",
                "142000020",
                "MANIJA GRIS",
                "1",
                **decorated,
                **{"LINEA DE PRODUCTO": "1202"},
            ),
            # The prefix the workbook's formula forgets in its last rows.
            _link(
                "311000006",
                "BT 75 MAC",
                "142000030",
                "TERM-PRT PROTECTOR",
                "2",
                **decorated,
                **{"LINEA DE PRODUCTO": "1204"},
            ),
            # No longer in force.
            _link(
                "311000006",
                "BT 75 MAC",
                "171000002",
                "ETIQUETA VIEJA",
                "1",
                **decorated,
                **{"LINEA DE PRODUCTO": "1401", "Fin Efectivo": "2024-12-31"},
            ),
            # A label the catalogue says is not an element.
            _link(
                "311000006",
                "BT 75 MAC",
                "171000003",
                "ETIQUETA INTERNA",
                "1",
                **decorated,
                **{"LINEA DE PRODUCTO": "1401"},
            ),
            formed,
            label,
            # What rows 28,520 to 29,260 of the real sheet look like.
            {"Artículo Matriz": "1"},
            {"Artículo Matriz": "1"},
        ],
        minutes=5,
    )
    # The same link to the formed battery, delivered by the other workbook too.
    _land(
        connection,
        "raw_product_struc",
        "qad_product_struc",
        "BAT TERM",
        [
            _link(
                "311000006",
                "BT 75 MAC",
                "231000001",
                "BF 75 6*5",
                "1",
                **{"Línea de productos": "3103", "Descripción #2": "DECO"},
            )
        ],
    )

    # -- Hand-maintained catalogues ---------------------------------------------
    def element(item: str, description: str, flag: str) -> dict[str, Any]:
        return {
            "Número Artículo": item,
            "Descripción": description,
            "Línea de productos": "1401",
            "Grpo": "ETIQUETA FRONTAL",
            "Tipo de artículo": "MAC",
            "elemento deco": flag,
        }

    _land(
        connection,
        "raw_manual_sheets",
        "manual_sheets",
        "ELEMENT DECO",
        [
            element("171000001", "ETIQUETA FRONTAL MAC", "SI"),
            element("142000010", "TAPA TERMINAL ROJA", "SI"),
            element("142000020", "MANIJA GRIS", "SI"),
            element("142000030", "TERM-PRT PROTECTOR", "SI"),
            element("171000002", "ETIQUETA VIEJA", "SI"),
            element("171000003", "ETIQUETA INTERNA", "NO"),
        ],
        dataset_key="element_deco",
    )
    _land(
        connection,
        "raw_manual_sheets",
        "manual_sheets",
        "Clasif_Poly",
        [
            # First table to the left, category table to the right: one sheet.
            {
                "Número Artículo": "121000001",
                "Descripción": "CUBIERTA 75",
                "Campo Equs": "CUB 1P",
                "CUENTA STATS": "COVRPROD_SLI1",
                "FACTOR EQU": "0.51",
                "Campo Equs #2": "CUB 1P",
                "CUENTA STATS #2": "COVRPROD_SLI1",
                "FACTOR EQU #2": "0.51",
            },
            {
                "Número Artículo": "142000020",
                "Descripción": "MANIJA GRIS",
                "Campo Equs": "MANIJA",
                "CUENTA STATS": "HANDPROD",
                "FACTOR EQU": "0.5",
                "Campo Equs #2": "MANIJA",
                "CUENTA STATS #2": "HANDPROD",
                "FACTOR EQU #2": "0.11",
            },
            {
                "Número Artículo": "150000001",
                "Descripción": "RESINA",
                "Campo Equs": "NA",
                "CUENTA STATS": "NA",
                "FACTOR EQU": "0",
                "Campo Equs #2": "NA",
                "CUENTA STATS #2": "NA",
                "FACTOR EQU #2": "0",
            },
            # Listed a second time, with another factor: the first row is used.
            {
                "Número Artículo": "121000001",
                "Descripción": "CUBIERTA 75",
                "Campo Equs": "CUB 1P",
                "CUENTA STATS": "COVRPROD_SLI1",
                "FACTOR EQU": "0.6",
            },
        ],
        dataset_key="clasif_poly",
    )
    _land(
        connection,
        "raw_manual_sheets",
        "manual_sheets",
        "Lineas Produccion",
        [
            {
                "Línea": line,
                "Descripción": f"LINEA {line}",
                "Almac": warehouse,
                "Planificación de Unidades por Hora": "120",
            }
            for line, warehouse in (
                ("ENSLIN01", "1515"),
                ("FAFORM02", "1515"),
                ("OEMPSOV1", "1515"),
                ("POLINY05", "1010"),
            )
        ],
        dataset_key="lineas_produccion",
    )
    plant, process, line_header = (
        "Planta",
        "Proceso (Base de datos energia y gas)",
        "Línea Producción (archivo transacciones por art)",
    )
    _land(
        connection,
        "raw_manual_sheets",
        "manual_sheets",
        "Hoja1",
        [
            {plant: "Ensamble", process: "Ensamble", line_header: "ENSLIN01"},
            # The two names the comparison sheet writes differently from QAD.
            {plant: "F&A", process: "FORMACION", line_header: "faform02"},
            {plant: "OREC", process: "Empaste Expanded", line_header: "OEMPSOV"},
        ],
        dataset_key="comparativo_lineas",
    )

    # -- Movements ---------------------------------------------------------------
    runs["receipts"] = _land(
        connection,
        "raw_rct_wo",
        "qad_rct_wo",
        "Datos",
        [
            _receipt(1001, "221000001", "10", "2026-01-05"),
            _receipt(
                1002,
                "231000001",
                "7",
                "2026-01-10T00:00:00",
                line="faform02",
                price="50",
                description="BF 75 6*5 FORM",
                product_line="3101",
            ),
            _receipt(
                1003,
                "231000001",
                "3",
                "2026-02-01",
                line="FAFORM02",
                price="55",
                description="BF 75 6*5 FORM",
                product_line="3101",
            ),
            # Same cost as the receipt before: not a change.
            _receipt(
                1004,
                "231000001",
                "-1",
                "2026-03-01",
                line="FAFORM02",
                price="55",
                description="BF 75 6*5 FORM",
                product_line="3101",
            ),
            # A cover QAD does not classify, although the plastic catalogue does.
            _receipt(
                1005,
                "121000001",
                "40",
                "2026-01-07",
                line="POLINY05",
                campo="N/A",
                description="CUBIERTA 75",
                product_line="1200",
                warehouse="1010",
            ),
            # An item no source describes, on a line no catalogue lists, and a
            # receipt QAD recorded without any line.
            _receipt(1006, "999000001", "1", "2026-01-08", line="NUEVA001", description=None),
            _receipt(1007, "221000001", "2", "2026-01-09", line=None),
            # A formed battery whose structure gives it no paste.
            _receipt(
                1008,
                "231000002",
                "4",
                "2026-01-11",
                line="FAFORM02",
                description="BF SIN ENSAMBLADA",
                product_line="3101",
            ),
            # Decorated batteries: one with pieces, one with no structure at all.
            _receipt(
                1009,
                "311000006",
                "6",
                "2026-01-12",
                line="FAFORM02",
                description="BT 75 MAC",
                product_line="3103",
            ),
            _receipt(
                1010,
                "311000099",
                "2",
                "2026-01-12",
                line="FAFORM02",
                description="BT SIN ESTRUCTURA",
                product_line="3103",
            ),
            # Not a receipt: no quantity.
            {"Número de transacción": 1011, "Fecha Efectiva": "2026-01-13", "Número Artículo": "X"},
        ],
    )
    # A later export brings receipt 1001 again, corrected.
    runs["receipts_again"] = _land(
        connection,
        "raw_rct_wo",
        "qad_rct_wo",
        "Datos",
        [_receipt(1001, "221000001", "12", "2026-01-05")],
        minutes=10,
    )

    _land(
        connection,
        "raw_facturas",
        "qad_facturas",
        "Datos",
        [
            _invoice("F1", "O1", "3", "C1", "COLOMBIA"),
            _invoice("F2", "O2", "-1", "C2", "Estados Unidos"),
        ],
    )
    _land(
        connection,
        "raw_facturas",
        "qad_facturas",
        "Datos",
        [_invoice("F1", "O1", "4", "C1", "COLOMBIA")],
        minutes=10,
    )
    return runs


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


@dataclass(frozen=True)
class Built:
    """A database whose Silver was built once from the rows above."""

    engine: Engine
    repository: PostgresSilverBuildRepository
    report: SilverBuildReport
    runs: dict[str, Any]


def test_nothing_is_built_from_an_empty_bronze(silver_engine: Engine) -> None:
    """Building from no completed load would empty Silver, so it is refused."""
    repository = PostgresSilverBuildRepository(silver_engine)

    with pytest.raises(PipelineError, match="no completed load"):
        repository.build()

    assert isinstance(repository, SilverBuildRepository)


class TestSilverRules:
    """One build of a small Bronze, read rule by rule."""

    @pytest.fixture(scope="class")
    def built(self, silver_engine: Engine) -> Built:
        """Land the rows and build Silver once for the whole class."""
        with silver_engine.begin() as connection:
            runs = _land_everything(connection)
        repository = PostgresSilverBuildRepository(silver_engine)
        return Built(silver_engine, repository, repository.build(), runs)

    @staticmethod
    def _rows(built: Built, statement: str, **parameters: Any) -> list[Any]:
        with built.engine.connect() as connection:
            return list(connection.execute(text(statement), parameters).all())

    def test_pasted_headers_stray_cells_and_junk_rows_are_left_out(self, built: Built) -> None:
        """Rule 1: a row that is not a link never becomes one, and is counted."""
        sheets = {sheet.sheet: sheet for sheet in built.report.structure}

        assert (sheets["PLACAS"].header_rows, sheets["PLACAS"].incomplete_rows) == (1, 1)
        assert (sheets["BAT FOR"].header_rows, sheets["BAT FOR"].incomplete_rows) == (1, 0)
        assert sheets["Datos"].incomplete_rows == 2
        # The "1" of the junk rows is not an item either.
        assert not self._rows(
            built, "SELECT 1 FROM silver.item_master WHERE numero_articulo IN ('1', '215000002')"
        )

    def test_a_repeated_link_is_kept_once(self, built: Built) -> None:
        """Rule 3: same parent, component, dates and quantity is the same link."""
        assert built.report.duplicates_dropped == {
            "BAT ENS": 1,
            "BAT FOR": 0,
            "BAT TERM": 0,
            "Datos": 1,
            "PASTA": 0,
            "PLACAS": 1,
        }
        links = self._rows(
            built,
            "SELECT count(*) FROM silver.bom_estructura "
            "WHERE articulo_matriz = '216000006' AND articulo_componente = '211000003'",
        )
        assert links[0][0] == 1

    def test_a_link_two_workbooks_deliver_is_kept_from_the_newest_load(self, built: Built) -> None:
        """BAT TERM and Datos both hold the link to the formed battery."""
        sheets = {sheet.sheet: sheet for sheet in built.report.structure}
        assert sheets["BAT TERM"].in_another_sheet == 1
        assert sheets["BAT TERM"].links == 0

        rows = self._rows(
            built,
            "SELECT source_sheet, source_run_id FROM silver.bom_estructura "
            "WHERE articulo_matriz = '311000006' AND articulo_componente = '231000001'",
        )
        assert [(row.source_sheet, row.source_run_id) for row in rows] == [
            ("Datos", built.runs["datos"])
        ]

    def test_only_the_latest_completed_load_of_a_sheet_counts(self, built: Built) -> None:
        """An older load is replaced, and a failed one landed nothing."""
        rows = self._rows(
            built,
            "SELECT cantidad_por, source_run_id FROM silver.bom_estructura "
            "WHERE articulo_matriz = '216000006' AND articulo_componente = '211000003'",
        )
        assert [(row.cantidad_por, row.source_run_id) for row in rows] == [
            (Decimal("0.06360000"), built.runs["placas"])
        ]

    def test_calculo_comes_from_bat_for_and_is_si_elsewhere(self, built: Built) -> None:
        """Rule 2: the one column that only one sheet has."""
        rows = self._rows(
            built,
            "SELECT articulo_matriz, articulo_componente, calculo FROM silver.bom_estructura "
            "WHERE source_sheet = 'BAT FOR' ORDER BY 1, 2",
        )
        assert [tuple(row) for row in rows] == [
            ("231000001", "130000001", "SI"),
            ("231000001", "221000001", "SI"),
            ("231000002", "221000099", "NO"),
            ("231000900", "221000001", "SI"),
            ("231000950", "221000001", "SI"),
            ("231000950", "222000003", "SI"),
            ("232000008", "222000003", "SI"),
        ]
        others = self._rows(
            built,
            "SELECT DISTINCT calculo FROM silver.bom_estructura WHERE source_sheet <> 'BAT FOR'",
        )
        assert [row.calculo for row in others] == ["SI"]

    def test_rows_below_a_pasted_header_are_read_by_that_header(self, built: Built) -> None:
        """Type and group are not crossed, and the unit is not taken for a description."""
        row = self._rows(
            built,
            "SELECT tipo_de_articulo, grpo, unidad_de_medida, descripcion2 "
            "FROM silver.item_master WHERE numero_articulo = '231000900'",
        )[0]
        assert tuple(row) == ("SLI-PMV", "24-12 LTH", "UN", None)

        above = self._rows(
            built,
            "SELECT tipo_de_articulo, grpo, descripcion2 FROM silver.item_master "
            "WHERE numero_articulo = '231000001'",
        )[0]
        assert tuple(above) == ("SLI-T", "7511AX", "FORM")
        sheets = {sheet.sheet: sheet for sheet in built.report.structure}
        assert sheets["BAT FOR"].reread_rows == 1

    def test_polarity_is_derived_for_pastes_and_plates_only(self, built: Built) -> None:
        """Rule 5, case by case."""
        rows = self._rows(
            built, "SELECT numero_articulo, polaridad FROM silver.item_master ORDER BY 1"
        )
        polarity = {row.numero_articulo: row.polaridad for row in rows}

        # Pastes: the type of sheet PASTA first, then the description.
        assert polarity["211000003"] == "+"
        assert polarity["211000004"] == "-"
        assert polarity["211000009"] == "-"
        assert polarity["21100000A"] == "+"
        # Plates: the sign in the description first, then the type.
        assert polarity["216000006"] == "+"
        assert polarity["216000007"] == "-"
        assert polarity["216000008"] == "-"
        assert polarity["21500000A"] == "+"
        # Nothing else has one.
        assert polarity["221000001"] is None
        assert polarity["210000001"] is None

    def test_paste_per_formed_battery(self, built: Built) -> None:
        """The conversion walks plate, assembled battery and formed battery."""
        rows = self._rows(
            built, "SELECT articulo_matriz, pasta_pos, pasta_neg FROM silver.td_form ORDER BY 1"
        )
        paste = {row.articulo_matriz: (row.pasta_pos, row.pasta_neg) for row in rows}

        # 36 x 0.0636 and 30 x 0.066; doubled if the repeated link had stayed.
        assert paste["231000001"] == (Decimal("2.2896"), Decimal("1.98"))
        # Inherited through the block read by the pasted header.
        assert paste["231000900"] == (Decimal("2.2896"), Decimal("1.98"))
        # 60 x 0.058: the imported plate has no product line and still counts.
        assert paste["232000008"] == (Decimal("3.48"), Decimal("0"))
        # Both of its assembled batteries count: 2.2896 + 3.48.
        assert paste["231000950"] == (Decimal("5.7696"), Decimal("1.98"))
        # CALCULO = NO.
        assert "231000002" not in paste

    def test_decoration_pieces_per_battery(self, built: Built) -> None:
        """Label 2 + terminal cap 2 + terminal protector 2, and nothing else."""
        rows = self._rows(built, "SELECT articulo_matriz, comp_deco FROM silver.td_deco")
        assert [(row.articulo_matriz, row.comp_deco) for row in rows] == [
            ("311000006", Decimal("6"))
        ]

        line = self._rows(
            built,
            "SELECT numero_articulo, linea_de_productos FROM silver.item_master "
            "WHERE numero_articulo IN ('142000030', '171000001') ORDER BY 1",
        )
        # From column LINEA DE PRODUCTO of sheet Datos, which overrides the
        # catalogue's 1401 for the protector.
        assert [tuple(row) for row in line] == [("142000030", "1204"), ("171000001", "1401")]

    def test_the_newest_copy_of_a_movement_wins(self, built: Built) -> None:
        """Receipts and invoice lines accumulate across loads by their natural key."""
        receipt = self._rows(
            built,
            "SELECT cambiar_cantidad_de_ubic, source_run_id FROM silver.reporte_qad "
            "WHERE numero_de_transaccion = 1001",
        )[0]
        assert tuple(receipt) == (Decimal("12"), built.runs["receipts_again"])

        invoice = self._rows(
            built,
            "SELECT num_fact, orden, linea, cantidad_fac, fecha_efectiva::text "
            "FROM silver.unit_ship ORDER BY 1",
        )
        assert [tuple(row) for row in invoice] == [
            ("F1", "O1", 1, Decimal("4"), "2026-01-21"),
            # A credit note stays negative.
            ("F2", "O2", 1, Decimal("-1"), "2026-01-21"),
        ]
        assert built.report.rows["reporte_qad"] == 10
        # The row without a quantity is not a receipt.
        assert built.report.receipts_left_out == 1
        assert built.report.invoice_lines_left_out == 0

    def test_not_applicable_stays_a_value(self, built: Built) -> None:
        """ "N/A" is a category, in the movement and in the catalogue."""
        receipt = self._rows(
            built,
            "SELECT campo_equs FROM silver.reporte_qad WHERE numero_de_transaccion = 1005",
        )[0]
        assert receipt.campo_equs == "N/A"
        categories = {
            row[0] for row in self._rows(built, "SELECT campo_equs FROM silver.campo_equs")
        }
        # The plastic catalogue spells it "NA"; both are kept as delivered.
        assert {"N/A", "NA", "SLI-S", "CUB 1P", "MANIJA"} <= categories

    def test_production_lines_are_homologated(self, built: Built) -> None:
        """Rule 6: one name per line, and the plant area of each."""
        rows = self._rows(
            built,
            "SELECT linea, planta, proceso_energia, almac, unidades_por_hora, descripcion "
            "FROM silver.lineas_produccion ORDER BY 1",
        )
        lines = {row.linea: row for row in rows}

        assert set(lines) == {"ENSLIN01", "FAFORM02", "NUEVA001", "OEMPSOV1", "POLINY05"}
        assert (lines["FAFORM02"].planta, lines["FAFORM02"].proceso_energia) == ("F&A", "FORMACION")
        assert lines["OEMPSOV1"].planta == "OREC"
        assert lines["ENSLIN01"].unidades_por_hora == 120
        # In no catalogue: created so that its receipt has a line to point at.
        assert lines["NUEVA001"].descripcion == SIN_DESCRIPCION
        assert lines["NUEVA001"].planta is None

        receipts = self._rows(
            built,
            "SELECT numero_de_transaccion, linea_produccion FROM silver.reporte_qad "
            "WHERE numero_de_transaccion IN (1002, 1007) ORDER BY 1",
        )
        # Written "faform02" in the export; and a receipt without a line stays.
        assert [tuple(row) for row in receipts] == [(1002, "FAFORM02"), (1007, None)]
        without_plant = {line.line: line.receipts for line in built.report.lines_without_plant}
        assert without_plant == {"NUEVA001": 1, "POLINY05": 1}

    def test_the_item_master_is_assembled_from_every_source(self, built: Built) -> None:
        """Rule 4: each attribute from the first source that has it."""
        rows = self._rows(
            built,
            "SELECT numero_articulo, descripcion, linea_de_productos, campo_equs, peso_neto, "
            "tipo_de_articulo FROM silver.item_master",
        )
        items = {row.numero_articulo: row for row in rows}

        # Described by the receipts before the structure gets a say.
        assert items["231000001"].descripcion == "BF 75 6*5 FORM"
        assert items["231000001"].tipo_de_articulo == "SLI-T"
        assert items["221000001"].campo_equs == "SLI-S"
        assert items["221000001"].peso_neto == Decimal("17.5")
        # Seen only as a component of a structure.
        assert items["130000001"].descripcion == "ACIDO"
        # Seen only in the plastic catalogue.
        assert items["150000001"].descripcion == "RESINA"
        # Seen in an invoice and a structure, never produced.
        assert items["311000006"].linea_de_productos == "3103"

    def test_what_nobody_describes_is_created_and_reported(self, built: Built) -> None:
        """Rule 10: every reference holds, and nothing is dropped to make it hold."""
        item = self._rows(
            built, "SELECT descripcion FROM silver.item_master WHERE numero_articulo = '999000001'"
        )[0]
        assert item.descripcion == SIN_DESCRIPCION
        assert built.report.items_without_description == ("999000001",)
        assert built.report.placeholders["item_master"] == 1
        assert built.report.placeholders["lineas_produccion"] == 1
        # Its receipt is in Silver.
        assert self._rows(
            built, "SELECT 1 FROM silver.reporte_qad WHERE numero_de_transaccion = 1006"
        )

    def test_customers_and_their_countries(self, built: Built) -> None:
        """A country written in capitals is the same country in title case."""
        rows = self._rows(built, "SELECT vendido_a, pais FROM silver.vendido_a ORDER BY 1")
        assert [tuple(row) for row in rows] == [("C1", "Colombia"), ("C2", "Estados Unidos")]

    def test_standard_cost_is_one_row_per_change(self, built: Built) -> None:
        """Rule 9: a cost ends the day the next one starts."""
        rows = self._rows(
            built,
            "SELECT vigente_desde::text, vigente_hasta::text, costo_std FROM silver.costo_std "
            "WHERE numero_articulo = '231000001' ORDER BY 1",
        )
        assert [tuple(row) for row in rows] == [
            ("2026-01-10", "2026-02-01", Decimal("50")),
            ("2026-02-01", None, Decimal("55")),
        ]

    def test_the_plastic_catalogue_is_checked_not_corrected(self, built: Built) -> None:
        """Rule 8: the first table is the catalogue; the second only checks it."""
        rows = self._rows(
            built,
            "SELECT numero_articulo, campo_equs, factor_equ FROM silver.clasif_poly ORDER BY 1",
        )
        assert [tuple(row) for row in rows] == [
            ("121000001", "CUB 1P", Decimal("0.51")),
            ("142000020", "MANIJA", Decimal("0.5")),
            ("150000001", "NA", Decimal("0")),
        ]
        mismatches = [
            (item.item, item.category, item.factor, item.category_factor)
            for item in built.report.poly_factor_mismatches
        ]
        assert mismatches == [("142000020", "MANIJA", Decimal("0.5"), Decimal("0.11"))]
        assert built.report.poly_repeated_items == ("121000001",)

    def test_the_report_names_what_needs_someone(self, built: Built) -> None:
        """The checks a person closing the month has to look at."""
        report = built.report

        assert [(item.item, item.quantity) for item in report.formed_without_paste] == [
            ("231000002", Decimal("4"))
        ]
        assert [(item.item, item.quantity) for item in report.decorated_without_pieces] == [
            ("311000099", Decimal("2"))
        ]
        assert [(item.item, item.poly_category) for item in report.na_with_poly_category] == [
            ("121000001", "CUB 1P")
        ]
        assert [
            (item.item, item.assembled, item.quantity)
            for item in report.formed_with_several_assembled
        ] == [("231000950", 2, Decimal("0"))]
        assert report.rows["td_form"] == 4
        assert report.rows["td_deco"] == 1
        assert report.rows["factores_equ"] == 16
        assert any("Item_Master is not landed" in note for note in report.notes)
        assert any("L.PRODUCCION is not landed" in note for note in report.notes)

    def test_building_again_changes_nothing(self, built: Built) -> None:
        """Idempotent: same Bronze, same Silver, row for row."""
        before = _checksums(built.engine)

        again = built.repository.build()

        assert _checksums(built.engine) == before
        assert again.rows == built.report.rows
        assert again.structure == built.report.structure

    def test_names_kept_by_hand_survive_a_build(self, built: Built) -> None:
        """The build fills an area name once; after that it belongs to people."""
        with built.engine.begin() as connection:
            connection.execute(
                text("UPDATE silver.planta SET nombre = 'Formación y acabado' WHERE planta = 'F&A'")
            )

        built.repository.build()

        name = self._rows(built, "SELECT nombre FROM silver.planta WHERE planta = 'F&A'")[0]
        assert name.nombre == "Formación y acabado"

    def test_a_group_is_read_from_l_produccion_when_it_is_landed(self, built: Built) -> None:
        """The optional sheet gives each line its group; without it there is none."""
        before = self._rows(built, "SELECT DISTINCT grupo FROM silver.lineas_produccion")
        assert [row.grupo for row in before] == [None]

        with built.engine.begin() as connection:
            _land(
                connection,
                "raw_manual_sheets",
                "manual_sheets",
                "L.PRODUCCION",
                [
                    {"GRUPO": "LINEA 1", "L.PRODUCCION": "ENSLIN01", "MES": "2026-01-01"},
                    {"GRUPO": "LINEA 1", "L.PRODUCCION": "ENSLIN01", "MES": "2026-02-01"},
                    {"GRUPO": "FORMACION", "L.PRODUCCION": "faform02", "MES": "2026-01-01"},
                ],
                minutes=20,
                dataset_key="l_produccion",
            )
        report = built.repository.build()

        rows = self._rows(
            built, "SELECT linea, grupo FROM silver.lineas_produccion WHERE grupo IS NOT NULL"
        )
        assert sorted(tuple(row) for row in rows) == [
            ("ENSLIN01", "LINEA 1"),
            ("FAFORM02", "FORMACION"),
        ]
        assert not any("L.PRODUCCION" in note for note in report.notes)

    def test_views_are_refreshed_even_when_it_cannot_be_done_concurrently(
        self, built: Built, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refused concurrent refresh falls back to the plain one, inside the build."""
        from energy.adapters import equs_silver_sql  # noqa: PLC0415

        monkeypatch.setattr(equs_silver_sql, "REFRESH_VIEWS_CONCURRENTLY", ("SELECT 1 / 0",))

        report = built.repository.build()

        assert not report.concurrent_refresh
        assert built.report.concurrent_refresh
        assert report.rows["td_form"] == built.report.rows["td_form"]

    def test_a_failed_step_leaves_silver_as_it_was(
        self, built: Built, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All-or-nothing: an error in a late step undoes the early ones."""
        from energy.adapters import equs_silver_sql  # noqa: PLC0415
        from energy.domain.exceptions import RepositoryError  # noqa: PLC0415

        before = _checksums(built.engine)
        broken = equs_silver_sql.Step("a step that cannot work", "SELECT 1 / 0")
        monkeypatch.setattr(
            equs_silver_sql, "BUILD_STEPS", (*equs_silver_sql.BUILD_STEPS[:-1], broken)
        )

        with pytest.raises(RepositoryError, match="nothing was changed") as raised:
            built.repository.build()

        assert raised.value.details["step"] == "a step that cannot work"
        assert _checksums(built.engine) == before
        assert (
            date(2026, 1, 5)
            == self._rows(
                built,
                "SELECT fecha_efectiva FROM silver.reporte_qad WHERE numero_de_transaccion = 1001",
            )[0].fecha_efectiva
        )


class TestSilverFromAPartialBronze:
    """A Bronze that holds only part of the sources: the build says what is missing."""

    @pytest.fixture(scope="class")
    def report(self, silver_engine: Engine) -> SilverBuildReport:
        """Land one receipt and an Item_Master with headers nobody has seen yet."""
        with silver_engine.begin() as connection:
            _land(
                connection,
                "raw_rct_wo",
                "qad_rct_wo",
                "Datos",
                [_receipt(1, "221000001", "5", "2026-01-05")],
            )
            _land(
                connection,
                "raw_item_master",
                "item_master",
                "Datos",
                [{"Item Number": "221000001", "Description": "BE 75 6*5"}],
            )
        return PostgresSilverBuildRepository(silver_engine).build()

    def test_the_notes_name_every_source_that_is_not_landed(
        self, report: SilverBuildReport
    ) -> None:
        """An empty table is explained, not left for someone to wonder about."""
        notes = " ".join(report.notes)

        assert "No product structure is landed" in notes
        assert "No invoice line is landed" in notes
        assert "No hand-maintained catalogue is landed" in notes
        assert "L.PRODUCCION is not landed" in notes
        assert "No production receipt" not in notes

    def test_an_item_master_with_unknown_headers_is_reported_not_ignored(
        self, report: SilverBuildReport
    ) -> None:
        """Its header texts are an assumption until the workbook is delivered."""
        assert any(
            "Item_Master is landed but none of its rows has the header" in note
            for note in report.notes
        )

    def test_what_was_landed_is_built(self, report: SilverBuildReport) -> None:
        """One receipt, the item and the line it names, and an empty structure."""
        assert report.rows["reporte_qad"] == 1
        assert report.rows["item_master"] == 1
        assert report.rows["lineas_produccion"] == 1
        assert report.rows["bom_estructura"] == 0
        assert report.structure == ()
        # Described by the receipt, since the Item_Master could not be read.
        assert report.items_without_description == ()
