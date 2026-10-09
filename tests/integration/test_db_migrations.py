"""Verify the Alembic migrations apply cleanly against real PostgreSQL.

Layer: tests (integration)

These assertions cannot be made against SQLite or a mock: schemas, TimescaleDB
hypertables, partial unique indexes and check constraints are all
PostgreSQL-specific. The suite runs against a live TimescaleDB instance - the CI
service container, or a testcontainer locally - and is skipped when neither is
available.

The migration chain is also verified in reverse: a downgrade that does not work
is discovered during an incident, which is the worst possible moment.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, inspect, text

from energy.core.constants import (
    ALL_SCHEMAS,
    BRONZE_SCHEMA,
    GOLD_SCHEMA,
    REPORTING_SCHEMA,
    SILVER_SCHEMA,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine

pytestmark = pytest.mark.integration

# Stated here rather than imported from the migration: a test that reads its
# expectations from the code under test cannot catch a table going missing.
LANDING_TABLES: tuple[str, ...] = (
    "raw_rct_wo",
    "raw_facturas",
    "raw_product_struc",
    "raw_item_master",
    "raw_manual_sheets",
)

# The Silver tables of the equivalent-batteries calculation, by what they hold.
EQUS_CATALOGUES: tuple[str, ...] = (
    "planta",
    "almacen",
    "lineas_produccion",
    "linea_de_productos",
    "campo_equs",
    "grupo_fam",
    "item_master",
    "costo_std",
    "vendido_a",
)
EQUS_STRUCTURES: tuple[str, ...] = (
    "bom_estructura",
    "element_deco",
    "clasif_poly",
    "costo_conversion",
)
EQUS_MOVEMENTS: tuple[str, ...] = ("reporte_qad", "unit_ship")
EQUS_CONFIGURATION: tuple[str, ...] = ("equs_version", "factores_equ", "instrucc")
EQUS_SILVER_TABLES: tuple[str, ...] = (
    *EQUS_CATALOGUES,
    *EQUS_STRUCTURES,
    *EQUS_MOVEMENTS,
    *EQUS_CONFIGURATION,
)
ENERGY_SILVER_TABLES: tuple[str, ...] = ("readings", "quarantine", "consolidation_audit")

# The Gold layer of the equivalent-batteries calculation.
EQUS_GOLD_TABLES: tuple[str, ...] = (
    "secciones_reporte",
    "filas_resumen_equ",
    "controles_calidad",
    "resumen_equ",
    "cierre_mes",
    "resumen_equ_historial",
)
EQUS_GOLD_VIEWS: tuple[str, ...] = (
    "v_reporte_qad",
    "v_reporte_qad_regla",
    "v_unit_ship_regla",
    "lineas_de_pdcto",
    "v_cantidad_cuenta",
    "v_resumen_equ",
    "v_equs_batt_mes",
    "inyeccion",
    "v_costo_por_eq",
)
EQUS_DQ_VIEWS: tuple[str, ...] = (
    "dq_sin_regla",
    "dq_doble_regla",
    "dq_sin_conversor",
    "dq_categoria_poly",
    "dq_linea_sin_planta",
    "dq_variacion",
)
EQUS_GOLD_FUNCTIONS: tuple[str, ...] = (
    "en_vigor",
    "versiones_rango",
    "resumen_equ_rango_mes",
    "resumen_equ_rango",
    "resumen_equ_cierre_mes",
    "inyeccion_rango_mes",
    "inyeccion_rango",
    "calidad_datos_rango",
)

# The views BI tools read (revision 0006), and the role that may read them.
REPORTING_VIEWS: tuple[str, ...] = (
    "cuentas",
    "articulos",
    "equs_mensual",
    "equs_batt_mensual",
    "equs_poly_mensual",
    "aporte_cuenta",
    "recibos_produccion",
    "calidad_datos_mensual",
    "cierres",
    "cierres_calidad",
    "equs_cerrado",
    "equs_cerrado_historial",
)
READER_ROLE = "reporting_reader"

EXPECTED_TABLES: dict[str, set[str]] = {
    "reference": {"plants", "meters", "shifts", "targets"},
    BRONZE_SCHEMA: {"raw_readings", "ingestion_runs", "raw_files", *LANDING_TABLES},
    SILVER_SCHEMA: {*ENERGY_SILVER_TABLES, *EQUS_SILVER_TABLES},
    GOLD_SCHEMA: {"kpi_hourly", "kpi_daily", "plant_daily_summary", *EQUS_GOLD_TABLES},
}

# Index name -> the payload key it is built on. The keys are header texts of the
# source workbooks, so the accents are part of the contract.
PAYLOAD_KEY_INDEXES: dict[str, str] = {
    "ix_raw_rct_wo_numero_de_transaccion": "Número de transacción",
    "ix_raw_rct_wo_fecha_efectiva": "Fecha Efectiva",
    "ix_raw_facturas_num_fact": "Núm Fact",
    "ix_raw_facturas_fecha_efectiva": "Fecha Efectiva",
    "ix_raw_product_struc_articulo_matriz": "Artículo Matriz",
}

LATEST_LOAD_INDEXES: tuple[str, ...] = (
    "ix_raw_product_struc_sheet_name_ingested",
    "ix_raw_item_master_sheet_name_ingested",
    "ix_raw_manual_sheets_dataset_key_ingested",
)

VALID_HASH = "a" * 64
UNKNOWN_RUN_ID = "00000000-0000-0000-0000-000000000000"
VALID_PAYLOAD = '{"Fecha Efectiva": "2025-12-10 00:00:00", "Campo Equs": "N/A"}'

EXPECTED_HYPERTABLES: set[str] = {
    "raw_readings",
    "readings",
    "kpi_hourly",
    "kpi_daily",
    "plant_daily_summary",
}


@pytest.fixture
def migrated_engine(migrated_database: str) -> Engine:
    """Return an engine bound to a database at Alembic head."""
    engine = create_engine(migrated_database, future=True)
    yield engine
    engine.dispose()


def test_migrations_apply_to_head(migrated_engine: Engine) -> None:
    """Every migration applies and the version table records the head revision."""
    with migrated_engine.connect() as connection:
        version = connection.execute(
            text("SELECT version_num FROM public.alembic_version")
        ).scalar_one()
    assert version == "0006"


def test_all_schemas_exist(migrated_engine: Engine) -> None:
    """The four application schemas are created."""
    inspector = inspect(migrated_engine)
    existing = set(inspector.get_schema_names())
    missing = set(ALL_SCHEMAS) - existing
    assert not missing, f"missing schemas: {sorted(missing)}"


@pytest.mark.parametrize(("schema", "tables"), sorted(EXPECTED_TABLES.items()))
def test_expected_tables_exist(migrated_engine: Engine, schema: str, tables: set[str]) -> None:
    """Each schema contains the tables the data dictionary documents."""
    inspector = inspect(migrated_engine)
    existing = set(inspector.get_table_names(schema=schema))
    missing = tables - existing
    assert not missing, f"missing tables in {schema}: {sorted(missing)}"


def test_required_extensions_are_installed(migrated_engine: Engine) -> None:
    """TimescaleDB and pgcrypto are installed."""
    with migrated_engine.connect() as connection:
        installed = {row[0] for row in connection.execute(text("SELECT extname FROM pg_extension"))}
    assert {"timescaledb", "pgcrypto"} <= installed


def test_hypertables_are_registered(migrated_engine: Engine) -> None:
    """The time-series tables are registered as TimescaleDB hypertables.

    Without this the schema still accepts writes but loses every partitioning,
    compression and retention guarantee the platform depends on - a failure that
    is invisible until the tables are large enough to matter.
    """
    with migrated_engine.connect() as connection:
        rows = connection.execute(
            text("SELECT hypertable_name FROM timescaledb_information.hypertables")
        ).all()
    registered = {row[0] for row in rows}
    missing = EXPECTED_HYPERTABLES - registered
    assert not missing, f"tables not converted to hypertables: {sorted(missing)}"


@pytest.fixture
def bronze_connection(bronze_engine: Engine) -> Connection:
    """Return a connection whose writes are rolled back when the test ends."""
    connection = bronze_engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


def _insert_run(connection: Connection, source_key: str = "qad_rct_wo") -> object:
    """Register an ingestion run and return its id."""
    return connection.execute(
        text(
            "INSERT INTO bronze.ingestion_runs "
            "(flow_name, source_key, source_system, plant_code) "
            "VALUES ('test_flow', :source_key, 'qad', '1515') RETURNING run_id"
        ),
        {"source_key": source_key},
    ).scalar_one()


def _insert_file(connection: Connection, source_key: str = "qad_rct_wo") -> object:
    """Catalogue an archived workbook and return its id."""
    return connection.execute(
        text(
            "INSERT INTO bronze.raw_files "
            "(content_hash, bucket, object_key, filename, content_type, size_bytes, "
            " plant_code, source_key, sheet_names) "
            "VALUES (:content_hash, 'bronze-raw', 'k', 'export.xlsx', 'application/x', "
            " 1024, '1515', :source_key, ARRAY['Datos', 'Info']) RETURNING raw_file_id"
        ),
        {"content_hash": VALID_HASH, "source_key": source_key},
    ).scalar_one()


def _insert_landing_row(
    connection: Connection,
    table: str,
    run_id: object,
    *,
    raw_file_id: object = None,
    content_hash: str = VALID_HASH,
    payload: str = VALID_PAYLOAD,
) -> None:
    """Land one row in a Bronze landing table."""
    extra_column = "dataset_key, " if table == "raw_manual_sheets" else ""
    extra_value = "'instrucc', " if table == "raw_manual_sheets" else ""
    connection.execute(
        # The table name comes from LANDING_TABLES, never from input.
        text(
            f"INSERT INTO bronze.{table} "  # noqa: S608
            f"({extra_column}ingestion_run_id, raw_file_id, source_system, source_uri, "
            " sheet_name, source_row, content_hash, plant_code, payload) "
            f"VALUES ({extra_value}:run_id, :raw_file_id, 'qad', 's3://bronze-raw/k', "
            " 'Datos', 2, :content_hash, '1515', CAST(:payload AS jsonb))"
        ),
        {
            "run_id": run_id,
            "raw_file_id": raw_file_id,
            "content_hash": content_hash,
            "payload": payload,
        },
    )


class TestBronzeRevision:
    """Revision 0002 on its own: structure, envelope checks and the append-only guard."""

    def test_creates_only_the_bronze_schema(self, bronze_engine: Engine) -> None:
        """Revision 0002 stands alone: the Bronze tables, and no other layer.

        The dimensions, Silver and Gold belong to later revisions. If one of
        them leaked back into this revision, a deployment stopped at 0002 would
        carry tables nothing has validated yet.
        """
        inspector = inspect(bronze_engine)

        schemas = set(inspector.get_schema_names()) & set(ALL_SCHEMAS)
        assert schemas == {BRONZE_SCHEMA}

        tables = set(inspector.get_table_names(schema=BRONZE_SCHEMA))
        assert tables == EXPECTED_TABLES[BRONZE_SCHEMA]

    def test_landing_tables_share_one_shape(self, bronze_engine: Engine) -> None:
        """Every landing table has the composite key and a non-unique hash index.

        The key includes ``ingested_at`` so the table can become a hypertable
        later; the hash index is not unique because Bronze keeps duplicates on
        purpose.
        """
        inspector = inspect(bronze_engine)

        for table in LANDING_TABLES:
            key = inspector.get_pk_constraint(table, schema=BRONZE_SCHEMA)
            assert key["name"] == f"pk_{table}"
            assert key["constrained_columns"] == [f"{table}_id", "ingested_at"]

            indexes = {
                index["name"]: index for index in inspector.get_indexes(table, BRONZE_SCHEMA)
            }
            for suffix in ("content_hash", "run", "raw_file"):
                assert f"ix_{table}_{suffix}" in indexes, f"{table} lacks its {suffix} index"
            assert not indexes[f"ix_{table}_content_hash"]["unique"]

            referred = {
                foreign_key["name"]: foreign_key["referred_table"]
                for foreign_key in inspector.get_foreign_keys(table, schema=BRONZE_SCHEMA)
            }
            assert referred == {
                f"fk_{table}_ingestion_run": "ingestion_runs",
                f"fk_{table}_raw_file": "raw_files",
            }

    def test_append_only_trigger_guards_every_landing_table(
        self, bronze_connection: Connection
    ) -> None:
        """The guard function exists and is attached to the landing tables only.

        The two ledgers must stay updatable - the loader moves a run from
        pending to completed - so a trigger on them would be as wrong as a
        missing one.
        """
        rows = bronze_connection.execute(
            text(
                "SELECT c.relname, pg_get_triggerdef(t.oid) AS definition "
                "FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_proc p ON p.oid = t.tgfoid "
                "JOIN pg_namespace pn ON pn.oid = p.pronamespace "
                "WHERE n.nspname = 'bronze' AND pn.nspname = 'bronze' "
                "AND p.proname = 'forbid_update' AND NOT t.tgisinternal"
            )
        ).all()

        guarded = {row.relname: row.definition for row in rows}
        assert set(guarded) == set(LANDING_TABLES)
        for table, definition in guarded.items():
            assert "BEFORE UPDATE" in definition, f"{table}: {definition}"
            assert "FOR EACH ROW" in definition, f"{table}: {definition}"

    def test_payload_key_indexes_exist(self, bronze_connection: Connection) -> None:
        """The expression indexes are built on the exact header texts of the exports.

        An index on a misspelt key is silently useless: it builds, and every
        lookup by the source's own key still falls back to a scan.
        """
        rows = bronze_connection.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'bronze'")
        ).all()
        definitions = {row.indexname: row.indexdef for row in rows}

        for index, payload_key in PAYLOAD_KEY_INDEXES.items():
            assert index in definitions, f"missing expression index {index}"
            assert f"payload ->> '{payload_key}'" in definitions[index]

        for index in LATEST_LOAD_INDEXES:
            assert index in definitions, f"missing index {index}"
            assert "ingested_at DESC" in definitions[index]

    def test_accepts_a_run_a_file_and_duplicate_rows(self, bronze_connection: Connection) -> None:
        """Valid provenance is accepted, and the same row may land twice.

        Duplicates are real in the source exports; collapsing them is a Silver
        decision, so Bronze must not refuse the second copy.
        """
        run_id = _insert_run(bronze_connection)
        raw_file_id = _insert_file(bronze_connection)

        for table in LANDING_TABLES:
            _insert_landing_row(bronze_connection, table, run_id, raw_file_id=raw_file_id)
            _insert_landing_row(bronze_connection, table, run_id, raw_file_id=raw_file_id)

            landed = bronze_connection.execute(
                text(f"SELECT count(*) FROM bronze.{table}")  # noqa: S608
            ).scalar_one()
            assert landed == 2, f"{table} kept {landed} of 2 identical rows"

    def test_rejects_malformed_provenance(self, bronze_connection: Connection) -> None:
        """A bad hash, a non-object payload and an unknown source key are refused.

        Bronze does not validate what a row says, only the envelope that makes
        it traceable. These are the envelope checks.
        """
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        run_id = _insert_run(bronze_connection)

        with (
            pytest.raises(IntegrityError, match="ck_raw_rct_wo_hash_format"),
            bronze_connection.begin_nested(),
        ):
            _insert_landing_row(bronze_connection, "raw_rct_wo", run_id, content_hash="ABCDEF")

        with (
            pytest.raises(IntegrityError, match="ck_raw_rct_wo_payload_object"),
            bronze_connection.begin_nested(),
        ):
            _insert_landing_row(bronze_connection, "raw_rct_wo", run_id, payload='["a", "list"]')

        with (
            pytest.raises(IntegrityError, match="ck_ingestion_runs_source_key"),
            bronze_connection.begin_nested(),
        ):
            _insert_run(bronze_connection, source_key="unknown_source")

        with (
            pytest.raises(IntegrityError, match="ck_raw_files_source_key"),
            bronze_connection.begin_nested(),
        ):
            _insert_file(bronze_connection, source_key="unknown_source")

        with (
            pytest.raises(IntegrityError, match="fk_raw_rct_wo_ingestion_run"),
            bronze_connection.begin_nested(),
        ):
            _insert_landing_row(bronze_connection, "raw_rct_wo", UNKNOWN_RUN_ID)

    def test_landing_rows_cannot_be_updated(self, bronze_connection: Connection) -> None:
        """An UPDATE on a landing table fails; a DELETE still works.

        Append-only is what lets a published figure be traced to the row it was
        computed from. DELETE is left open because that is how retention removes
        old rows.
        """
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        run_id = _insert_run(bronze_connection)

        for table in LANDING_TABLES:
            _insert_landing_row(bronze_connection, table, run_id)

            with (
                pytest.raises(IntegrityError, match=f"bronze.{table} is append-only"),
                bronze_connection.begin_nested(),
            ):
                bronze_connection.execute(
                    text(f"UPDATE bronze.{table} SET source_row = source_row + 1")  # noqa: S608
                )

            deleted = bronze_connection.execute(
                text(f"DELETE FROM bronze.{table}")  # noqa: S608
            ).rowcount
            assert deleted == 1, f"DELETE on {table} removed {deleted} rows"

    def test_ledgers_can_be_updated(self, bronze_connection: Connection) -> None:
        """The run ledger and the file catalogue are not append-only.

        The loader records progress by updating the run it opened.
        """
        run_id = _insert_run(bronze_connection)
        raw_file_id = _insert_file(bronze_connection)

        state = bronze_connection.execute(
            text(
                "UPDATE bronze.ingestion_runs "
                "SET state = 'completed', finished_at = now(), row_count = 10 "
                "WHERE run_id = :run_id RETURNING state"
            ),
            {"run_id": run_id},
        ).scalar_one()
        assert state == "completed"

        uploaded_by = bronze_connection.execute(
            text(
                "UPDATE bronze.raw_files SET uploaded_by = 'steward' "
                "WHERE raw_file_id = :raw_file_id RETURNING uploaded_by"
            ),
            {"raw_file_id": raw_file_id},
        ).scalar_one()
        assert uploaded_by == "steward"


@pytest.fixture
def silver_connection(silver_engine: Engine) -> Connection:
    """Return a connection whose writes are rolled back when the test ends."""
    connection = silver_engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


def _rejected(connection: Connection, statement: str, **parameters: object) -> str:
    """Run a statement that must be refused and return the database's message."""
    from sqlalchemy.exc import DBAPIError  # noqa: PLC0415

    savepoint = connection.begin_nested()
    try:
        connection.execute(text(statement), parameters)
    except DBAPIError as error:
        savepoint.rollback()
        return str(error.orig)
    savepoint.rollback()
    pytest.fail(f"the database accepted: {statement}")


class TestSilverRevision:
    """Revision 0003 for the equivalent-batteries tables: structure, rules and seed."""

    def test_creates_the_equs_tables_beside_the_energy_ones(self, silver_engine: Engine) -> None:
        """Eighteen EQUS tables and two materialized views, and no Gold yet."""
        inspector = inspect(silver_engine)

        assert GOLD_SCHEMA not in inspector.get_schema_names()
        tables = set(inspector.get_table_names(schema=SILVER_SCHEMA))
        assert tables == EXPECTED_TABLES[SILVER_SCHEMA]
        assert len(EQUS_SILVER_TABLES) == 18
        assert set(inspector.get_materialized_view_names(schema=SILVER_SCHEMA)) == {
            "td_form",
            "td_deco",
        }

    def test_tables_are_keyed_by_what_the_business_calls_them(self, silver_engine: Engine) -> None:
        """Natural keys, and item codes as text: 21500000A is a real item."""
        inspector = inspect(silver_engine)
        keys = {
            table: inspector.get_pk_constraint(table, schema=SILVER_SCHEMA)["constrained_columns"]
            for table in EQUS_SILVER_TABLES
        }

        assert keys["item_master"] == ["numero_articulo"]
        assert keys["reporte_qad"] == ["numero_de_transaccion"]
        assert keys["unit_ship"] == ["num_fact", "orden", "linea"]
        assert keys["costo_std"] == ["numero_articulo", "vigente_desde"]
        assert keys["costo_conversion"] == ["mes", "planta", "proceso"]
        assert keys["factores_equ"] == ["version", "cuenta_stats"]
        assert keys["instrucc"] == ["version", "cuenta_stats", "regla_n"]
        assert keys["bom_estructura"] == ["bom_id"]
        assert all(keys.values())

        columns = {
            column["name"]: str(column["type"])
            for column in inspector.get_columns("item_master", schema=SILVER_SCHEMA)
        }
        assert columns["numero_articulo"] == "TEXT"

    def test_every_reference_is_a_foreign_key(self, silver_engine: Engine) -> None:
        """The structure points at the item master twice; lineage points at Bronze."""
        inspector = inspect(silver_engine)

        def targets(table: str) -> set[tuple[str, str, str]]:
            return {
                (key["constrained_columns"][0], key["referred_schema"], key["referred_table"])
                for key in inspector.get_foreign_keys(table, schema=SILVER_SCHEMA)
            }

        assert targets("bom_estructura") == {
            ("articulo_matriz", "silver", "item_master"),
            ("articulo_componente", "silver", "item_master"),
            ("source_run_id", "bronze", "ingestion_runs"),
        }
        assert targets("reporte_qad") == {
            ("numero_articulo", "silver", "item_master"),
            ("almac", "silver", "almacen"),
            ("linea_produccion", "silver", "lineas_produccion"),
            ("source_run_id", "bronze", "ingestion_runs"),
        }
        assert targets("unit_ship") == {
            ("vendido_a", "silver", "vendido_a"),
            ("numero_articulo", "silver", "item_master"),
            ("source_run_id", "bronze", "ingestion_runs"),
        }
        assert targets("item_master") == {
            ("linea_de_productos", "silver", "linea_de_productos"),
            ("campo_equs", "silver", "campo_equs"),
            ("familia_placa", "silver", "grupo_fam"),
        }
        assert targets("lineas_produccion") == {
            ("almac", "silver", "almacen"),
            ("planta", "silver", "planta"),
        }
        assert targets("costo_std") == {("numero_articulo", "silver", "item_master")}
        assert targets("element_deco") == {("numero_articulo", "silver", "item_master")}
        assert targets("clasif_poly") == {("numero_articulo", "silver", "item_master")}
        assert targets("costo_conversion") == {("planta", "silver", "planta")}
        assert targets("factores_equ") == {("version", "silver", "equs_version")}
        assert targets("instrucc") == {("version", "silver", "factores_equ")}

    def test_indexes_the_calculation_reads_by_exist(self, silver_connection: Connection) -> None:
        """Movements by date, item and line; structure by parent and by component."""
        rows = silver_connection.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'silver'")
        ).all()
        definitions = {row.indexname: row.indexdef for row in rows}

        assert "(articulo_matriz)" in definitions["ix_bom_estructura_matriz"]
        assert "(articulo_componente)" in definitions["ix_bom_estructura_componente"]
        assert "(fecha_efectiva, tipo_transaccion)" in definitions["ix_reporte_qad_fecha_tipo"]
        assert "(numero_articulo)" in definitions["ix_reporte_qad_articulo"]
        assert "(linea_produccion)" in definitions["ix_reporte_qad_linea"]
        assert "(fecha_efectiva)" in definitions["ix_unit_ship_fecha"]
        assert "(numero_articulo)" in definitions["ix_unit_ship_articulo"]
        # What lets each conversion table be refreshed while it is being read.
        assert definitions["uq_td_form_articulo"].startswith("CREATE UNIQUE INDEX")
        assert definitions["uq_td_deco_articulo"].startswith("CREATE UNIQUE INDEX")

    def test_conversion_tables_refresh_concurrently(self, silver_connection: Connection) -> None:
        """Both views are created populated, which a concurrent refresh requires."""
        silver_connection.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY silver.td_form"))
        silver_connection.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY silver.td_deco"))

    def test_version_one_reproduces_the_factors_of_the_workbook(
        self, silver_connection: Connection
    ) -> None:
        """Sixteen drivers of one version, STD FY26, open-ended from October 2025."""
        version = silver_connection.execute(
            text("SELECT nombre, vigente_desde::text, vigente_hasta FROM silver.equs_version")
        ).one()
        assert tuple(version) == ("STD FY26", "2025-10-01", None)

        rows = silver_connection.execute(
            text(
                "SELECT cuenta_stats, proceso, fuente, factor, peso, suma_eq "
                "FROM silver.factores_equ WHERE version = 1"
            )
        ).all()
        factors = {row.cuenta_stats: row for row in rows}

        assert len(factors) == 16
        assert float(factors["COSPRODU_TRUCK"].factor) == pytest.approx(0.7611, abs=1e-12)
        assert float(factors["OXPROD_KG"].factor) == pytest.approx(0.0121711, abs=5e-8)
        assert float(factors["POS_ACT_MTL"].factor) == pytest.approx(0.0430678, abs=5e-8)
        # The other factors, as column G of sheet RESUMEN EQU shows them.
        assert float(factors["GRIDS_REG_POS"].factor) == pytest.approx(0.03207471428571429)
        assert float(factors["GRIDS_CAST"].factor) == pytest.approx(0.001805)
        assert float(factors["GRIDS_NEG"].factor) == pytest.approx(0.022270476190476195)
        assert float(factors["PLATEPRD_SLI"].factor) == pytest.approx(0.0021190476190476194)
        assert float(factors["PLATEPRD_TRUCK"].factor) == pytest.approx(0.002415714285714286)
        assert float(factors["COSPRODU_SLI"].factor) == pytest.approx(0.354)
        assert float(factors["COMP_DEC"].factor) == pytest.approx(0.02634032634032634)
        assert float(factors["CARTON_BATT"].factor) == pytest.approx(0.3371561771561772)
        assert float(factors["UNITSHIP"].factor) == pytest.approx(0.041)

        assert factors["UNITSHIP"].fuente == "FACTURACION"
        assert factors["POS_ACT_MTL"].fuente == "MAT_ACTIVO"
        assert factors["COMP_DEC"].fuente == "COMP_DECO"
        informative = {name for name, row in factors.items() if not row.suma_eq}
        assert informative == {"15BATPRD", "FORM_PRD_SLI", "FORM_PRD_TRUCK", "LEADPROD"}
        assert all(factors[name].peso == 0 and factors[name].factor == 0 for name in informative)

    def test_the_weights_of_the_processes_add_up_to_one(
        self, silver_connection: Connection
    ) -> None:
        """Several drivers share one weight, so each (process, weight) counts once."""
        rows = silver_connection.execute(
            text(
                "SELECT proceso, sum(peso) AS peso FROM ("
                " SELECT DISTINCT proceso, peso FROM silver.factores_equ"
                " WHERE version = 1 AND suma_eq) AS pares GROUP BY proceso"
            )
        ).all()
        weights = {row.proceso: row.peso for row in rows}

        assert {name: float(value) for name, value in weights.items()} == {
            "OREC": pytest.approx(0.346),
            "ENSAMBLE": pytest.approx(0.354),
            "FORMACION": pytest.approx(0.146),
            "DECORADO": pytest.approx(0.113),
            "SHIPPING": pytest.approx(0.041),
        }
        assert sum(weights.values()) == 1

    def test_the_rules_of_version_one(self, silver_connection: Connection) -> None:
        """Seventeen rules, as EQUS_CONTEXT section 6.5 lists them."""
        rows = silver_connection.execute(
            text(
                "SELECT cuenta_stats, regla_n, linea_de_productos, campo_equs, "
                "campo_equs_excluir, prefijo_linea, signo FROM silver.instrucc "
                "WHERE version = 1 ORDER BY cuenta_stats, regla_n"
            )
        ).all()
        rules: dict[str, list[tuple[object, ...]]] = {}
        for row in rows:
            rules.setdefault(row.cuenta_stats, []).append(tuple(row)[2:6])

        assert len(rows) == 17
        assert {row.signo for row in rows} == {1}
        assert rules["OXPROD_KG"] == [(None, "OXIDO", None, None)]
        assert rules["GRIDS_CAST"] == [("2102", None, None, None)]
        assert rules["COSPRODU_SLI"] == [("2201", None, "TRUCK-B", None)]
        assert rules["COSPRODU_TRUCK"] == [("2201", "TRUCK-B", None, None)]
        assert rules["COMP_DEC"] == [
            ("3102", None, None, None),
            ("3103", None, None, None),
            ("3104", None, None, None),
        ]
        assert rules["UNITSHIP"] == [("3103", None, None, None), ("3104", None, None, None)]
        assert rules["LEADPROD"] == [(None, None, None, "RCREFI")]
        # A driver that is counted but has no rule contributes nothing.
        assert "CARTON_BATT" not in rules
        assert "15BATPRD" not in rules

    def test_a_factor_cannot_be_written_by_hand(self, silver_connection: Connection) -> None:
        """The factor follows its three terms; nobody can set it apart from them."""
        message = _rejected(
            silver_connection,
            "UPDATE silver.factores_equ SET factor = 1 WHERE cuenta_stats = 'OXPROD_KG'",
        )
        assert "generated" in message

        factor = silver_connection.execute(
            text(
                "UPDATE silver.factores_equ SET multiplicador = 2 "
                "WHERE version = 1 AND cuenta_stats = 'COSPRODU_SLI' RETURNING factor"
            )
        ).scalar_one()
        assert float(factor) == pytest.approx(0.708)

    @pytest.mark.parametrize(
        ("statement", "constraint"),
        [
            (
                "UPDATE silver.factores_equ SET fuente = 'INVENTARIO' "
                "WHERE cuenta_stats = 'UNITSHIP'",
                "ck_factores_equ_fuente",
            ),
            (
                # Zero is refused too, by the division inside the factor itself.
                "UPDATE silver.factores_equ SET estandar_por_bat = -1 "
                "WHERE cuenta_stats = 'UNITSHIP'",
                "ck_factores_equ_estandar",
            ),
            (
                "UPDATE silver.instrucc SET signo = 0 WHERE cuenta_stats = 'UNITSHIP'",
                "ck_instrucc_signo",
            ),
            (
                "INSERT INTO silver.item_master (numero_articulo, polaridad) VALUES ('X', '±')",
                "ck_item_master_polaridad",
            ),
            (
                "INSERT INTO silver.instrucc (version, cuenta_stats, regla_n) "
                "VALUES (1, 'NO_SUCH_DRIVER', 1)",
                "fk_instrucc_factor",
            ),
        ],
    )
    def test_checks_reject_what_the_calculation_cannot_use(
        self, silver_connection: Connection, statement: str, constraint: str
    ) -> None:
        """Values outside the short lists of the specification are refused."""
        assert constraint in _rejected(silver_connection, statement)

    def test_a_structure_link_is_unique_by_its_business_key(
        self, silver_connection: Connection
    ) -> None:
        """Parent, component, validity and quantity: a second copy is refused.

        Two links with no dates at all are the same link, which is why the
        constraint treats empty dates as equal.
        """
        run_id = _insert_run(silver_connection, "qad_product_struc")
        silver_connection.execute(
            text(
                "INSERT INTO silver.item_master (numero_articulo, polaridad) "
                "VALUES ('21500000A', '+'), ('21100000A', NULL)"
            )
        )
        link = (
            "INSERT INTO silver.bom_estructura "
            "(articulo_matriz, articulo_componente, cantidad_por, calculo, source_sheet, "
            " source_run_id) VALUES ('21500000A', '21100000A', :cantidad, :calculo, 'PLACAS', "
            " :run_id)"
        )
        silver_connection.execute(
            text(link), {"cantidad": 0.058, "calculo": "SI", "run_id": run_id}
        )

        repeated = _rejected(silver_connection, link, cantidad=0.058, calculo="SI", run_id=run_id)
        assert "uq_bom_estructura_enlace" in repeated
        # Another quantity is another link.
        silver_connection.execute(text(link), {"cantidad": 0.06, "calculo": "SI", "run_id": run_id})

        not_a_flag = _rejected(silver_connection, link, cantidad=1, calculo="X", run_id=run_id)
        assert "ck_bom_estructura_calculo" in not_a_flag
        unknown_item = _rejected(
            silver_connection,
            "INSERT INTO silver.bom_estructura (articulo_matriz, articulo_componente, "
            "cantidad_por, source_sheet, source_run_id) "
            "VALUES ('21500000A', 'NOT-AN-ITEM', 1, 'PLACAS', :run_id)",
            run_id=run_id,
        )
        assert "fk_bom_estructura_articulo_componente" in unknown_item


@pytest.fixture
def gold_connection(gold_engine: Engine) -> Connection:
    """Return a connection whose writes are rolled back when the test ends."""
    connection = gold_engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


def _definitions(connection: Connection) -> dict[str, str]:
    """Return the text of every view and function of the gold schema, by name."""
    views = connection.execute(
        text("SELECT viewname, definition FROM pg_views WHERE schemaname = 'gold'")
    ).all()
    functions = connection.execute(
        text(
            "SELECT p.proname, p.prosrc FROM pg_proc AS p "
            "JOIN pg_namespace AS n ON n.oid = p.pronamespace WHERE n.nspname = 'gold'"
        )
    ).all()
    return {row[0]: row[1] for row in [*views, *functions]}


class TestGoldRevision:
    """Revision 0004 for the equivalent-batteries Gold layer: objects, layout and rules."""

    def test_creates_the_equs_objects_beside_the_energy_tables(
        self, gold_connection: Connection, gold_engine: Engine
    ) -> None:
        """Six tables, fifteen views and eight functions; nothing materialized."""
        inspector = inspect(gold_engine)
        assert set(inspector.get_table_names(schema=GOLD_SCHEMA)) == EXPECTED_TABLES[GOLD_SCHEMA]
        assert set(inspector.get_view_names(schema=GOLD_SCHEMA)) == {
            *EQUS_GOLD_VIEWS,
            *EQUS_DQ_VIEWS,
        }
        assert inspector.get_materialized_view_names(schema=GOLD_SCHEMA) == []
        functions = {
            row[0]
            for row in gold_connection.execute(
                text(
                    "SELECT p.proname FROM pg_proc AS p "
                    "JOIN pg_namespace AS n ON n.oid = p.pronamespace WHERE n.nspname = 'gold'"
                )
            )
        }
        assert functions == set(EQUS_GOLD_FUNCTIONS)

    def test_no_view_writes_a_constant_of_the_equation(self, gold_connection: Connection) -> None:
        """Factors, weights, Campo Equs values and product lines come from Silver.

        The values are read from the seeded configuration itself, so a value
        added later is checked too. The only literals allowed are RCT-WO, the
        channel Venta, warehouse 1010 and the product-line range of check 1.
        """
        rules = gold_connection.execute(
            text(
                "SELECT linea_de_productos, campo_equs, campo_equs_excluir, prefijo_linea "
                "FROM silver.instrucc"
            )
        ).all()
        words = {value for row in rules for value in row if value}
        numbers = {
            str(value.normalize())
            for row in gold_connection.execute(
                text(
                    "SELECT estandar_por_bat, multiplicador, peso FROM silver.factores_equ "
                    "WHERE peso > 0"
                )
            )
            for value in row
            if value not in (0, 1)
        }
        categories = {
            row[0]
            for row in gold_connection.execute(text("SELECT campo_equs FROM silver.campo_equs"))
        }
        assert {"OXIDO", "TRUCK-B", "2201", "3104", "RCREFI"} <= words
        assert {"6.08", "0.074", "2.15", "3.39", "4.29", "12.8"} <= numbers

        allowed = {("dq_sin_regla", "3104")}
        found = [
            (name, literal)
            for name, body in _definitions(gold_connection).items()
            for literal in sorted(words | categories)
            if f"'{literal}'" in body and (name, literal) not in allowed
        ] + [
            (name, number)
            for name, body in _definitions(gold_connection).items()
            for number in sorted(numbers)
            if re.search(rf"(?<![\d.]){re.escape(number)}(?![\d])", body)
        ]
        assert found == []

    def test_the_layout_of_resumen_equ_is_data(self, gold_connection: Connection) -> None:
        """Column F of RESUMEN EQU, the rows of its drivers and the checks before a close."""
        sections = gold_connection.execute(
            text("SELECT proceso, planta, area_planta FROM gold.secciones_reporte ORDER BY orden")
        ).all()
        assert [tuple(row) for row in sections] == [
            ("OREC", "OREC", "OREC"),
            ("ENSAMBLE", "ENSAMBLE", "Ensamble"),
            ("FORMACION", "F&A", "F&A"),
            ("DECORADO", "F&A", "F&A"),
            ("SHIPPING", "DESPACHOS", None),
            ("INFO", "INFORMATIVOS", None),
            ("POLY", "EQUS POLY", "Componentes"),
        ]
        rows = dict(
            gold_connection.execute(text("SELECT cuenta_stats, fila FROM gold.filas_resumen_equ"))
            .tuples()
            .all()
        )
        assert rows["OXPROD_KG"] == 2
        assert rows["UNITSHIP"] == 16
        assert rows["LEADPROD"] == 18
        assert rows["CONTPROD_CAR"] == 20
        assert rows["Med_Comp_Prod"] == 30
        drivers = {
            row[0]
            for row in gold_connection.execute(text("SELECT cuenta_stats FROM silver.factores_equ"))
        }
        assert drivers <= set(rows)

        checks = gold_connection.execute(
            text("SELECT vista, bloqueante FROM gold.controles_calidad ORDER BY orden")
        ).all()
        assert [row[0] for row in checks] == list(EQUS_DQ_VIEWS)
        assert [row[0] for row in checks if row[1]] == list(EQUS_DQ_VIEWS[:3])

    def test_the_plant_areas_of_the_sections_exist_before_any_build(
        self, gold_connection: Connection
    ) -> None:
        """The sections point at silver.planta, which the migration fills for them."""
        areas = {
            row[0] for row in gold_connection.execute(text("SELECT planta FROM silver.planta"))
        }
        assert areas == {"OREC", "Ensamble", "F&A", "Componentes"}
        bad_area = _rejected(
            gold_connection,
            "UPDATE gold.secciones_reporte SET area_planta = 'Nowhere' WHERE proceso = 'OREC'",
        )
        assert "fk_secciones_reporte_area_planta" in bad_area
        bad_process = _rejected(
            gold_connection,
            "INSERT INTO gold.secciones_reporte VALUES ('PINTURA', 'PINTURA', NULL, 9)",
        )
        assert "ck_secciones_reporte_proceso" in bad_process

    def test_a_close_names_its_factor_and_its_month(self, gold_connection: Connection) -> None:
        """The frozen close points at silver.factores_equ and is keyed by first days."""
        unknown = _rejected(
            gold_connection,
            "INSERT INTO gold.resumen_equ (mes, version, cuenta_stats, cantidad, equs, closed_by) "
            "VALUES ('2026-09-01', 1, 'NO_SUCH_DRIVER', 1, 1, 'test')",
        )
        assert "fk_resumen_equ_factor" in unknown
        mid_month = _rejected(
            gold_connection,
            "INSERT INTO gold.resumen_equ (mes, version, cuenta_stats, cantidad, equs, closed_by) "
            "VALUES ('2026-09-15', 1, 'OXPROD_KG', 1, 1, 'test')",
        )
        assert "ck_resumen_equ_primer_dia" in mid_month
        gold_connection.execute(
            text(
                "INSERT INTO gold.resumen_equ (mes, version, cuenta_stats, cantidad, equs, "
                "closed_by) VALUES ('2026-09-01', 1, 'OXPROD_KG', 1, 1, 'test')"
            )
        )
        # A version a close uses can no longer be deleted.
        in_use = _rejected(gold_connection, "DELETE FROM silver.factores_equ WHERE version = 1")
        assert "fk_resumen_equ_factor" in in_use or "fk_instrucc_factor" in in_use

    def test_an_empty_database_answers_every_question(self, gold_connection: Connection) -> None:
        """No production yet: every view runs and the range functions give zeros."""
        for view in (*EQUS_GOLD_VIEWS, *EQUS_DQ_VIEWS):
            # The names are this module's own constants.
            gold_connection.execute(text(f"SELECT count(*) FROM gold.{view}"))  # noqa: S608
        drivers = gold_connection.execute(
            text(
                "SELECT count(*), sum(cantidad), sum(equs) "
                "FROM gold.resumen_equ_rango('2026-09-01', '2026-09-30')"
            )
        ).one()
        assert tuple(drivers) == (16, 0, 0)
        versions = gold_connection.execute(
            text("SELECT mes, version FROM gold.versiones_rango('2025-09-15', '2025-10-02')")
        ).all()
        assert [(row[0].isoformat(), row[1]) for row in versions] == [
            ("2025-09-01", None),
            ("2025-10-01", 1),
        ]
        quality = gold_connection.execute(
            text("SELECT vista, filas FROM gold.calidad_datos_rango('2026-09-01', '2026-09-30')")
        ).all()
        assert [tuple(row) for row in quality] == [(view, 0) for view in EQUS_DQ_VIEWS]


class TestReportingRevision:
    """Revision 0006: views for BI tools, readable by one role and nothing more."""

    def test_holds_only_the_reporting_views(self, gold_engine: Engine) -> None:
        """Twelve views, no table, and the fiscal-year function."""
        inspector = inspect(gold_engine)
        assert inspector.get_table_names(schema=REPORTING_SCHEMA) == []
        assert set(inspector.get_view_names(schema=REPORTING_SCHEMA)) == set(REPORTING_VIEWS)
        with gold_engine.connect() as connection:
            functions = connection.execute(
                text(
                    "SELECT p.proname FROM pg_proc AS p "
                    "JOIN pg_namespace AS n ON n.oid = p.pronamespace WHERE n.nspname = :s"
                ),
                {"s": REPORTING_SCHEMA},
            ).scalars()
            assert set(functions) == {"anio_fiscal"}

    def test_the_reader_role_can_read_the_views_and_nothing_else(self, gold_engine: Engine) -> None:
        """A role without login, granted the views, and no schema of the medallion."""
        with gold_engine.connect() as connection:
            can_login = connection.execute(
                text("SELECT rolcanlogin FROM pg_roles WHERE rolname = :r"), {"r": READER_ROLE}
            ).scalar_one()
            assert can_login is False
            for view in REPORTING_VIEWS:
                granted = connection.execute(
                    text("SELECT has_table_privilege(:r, :v, 'SELECT')"),
                    {"r": READER_ROLE, "v": f"{REPORTING_SCHEMA}.{view}"},
                ).scalar_one()
                assert granted, view
            for schema in (BRONZE_SCHEMA, SILVER_SCHEMA, GOLD_SCHEMA, "reference"):
                usage = connection.execute(
                    text("SELECT has_schema_privilege(:r, :s, 'USAGE')"),
                    {"r": READER_ROLE, "s": schema},
                ).scalar_one()
                assert usage is False, schema

    def test_an_empty_database_answers_every_view(self, gold_engine: Engine) -> None:
        """No production yet: every view runs and returns nothing to report."""
        with gold_engine.connect() as connection:
            for view in REPORTING_VIEWS:
                # The names are this module's own constants.
                connection.execute(text(f"SELECT count(*) FROM {REPORTING_SCHEMA}.{view}"))  # noqa: S608
            months = connection.execute(
                text("SELECT count(*) FROM reporting.equs_mensual")
            ).scalar_one()
        assert months == 0


def test_gold_downgrades_to_silver_and_up_again(database_url: str) -> None:
    """Revision 0004 alone goes down and up, and takes back the plant areas it added."""
    import os  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    root = Path(__file__).resolve().parent.parent.parent
    config = Config(toml_file=str(root / "pyproject.toml"))
    config.set_main_option("script_location", str(root / "migrations"))

    engine = create_engine(database_url, future=True)
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "-1")
        command.upgrade(config, "head")
        command.downgrade(config, "0003")
        with engine.connect() as connection:
            areas = connection.execute(text("SELECT count(*) FROM silver.planta")).scalar_one()
        schemas = set(inspect(engine).get_schema_names())
        assert GOLD_SCHEMA not in schemas
        assert REPORTING_SCHEMA not in schemas
        assert areas == 0
        command.upgrade(config, "head")
        with engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one()
        assert version == "0006"
    finally:
        with engine.begin() as connection:
            for schema in reversed(ALL_SCHEMAS):
                connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
        engine.dispose()
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def test_silver_readings_enforces_grid_alignment(migrated_engine: Engine) -> None:
    """An unaligned Silver timestamp is rejected by a check constraint.

    The canonical grid is enforced by the database, not only by the pipeline, so
    a direct insert cannot corrupt it.
    """
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    with migrated_engine.begin() as connection:
        plant_id = connection.execute(
            text(
                "INSERT INTO reference.plants (code, name, timezone) "
                "VALUES ('TEST-GRID', 'Grid Test Plant', 'UTC') RETURNING plant_id"
            )
        ).scalar_one()
        meter_id = connection.execute(
            text(
                "INSERT INTO reference.meters (plant_id, tag, commodity, unit) "
                "VALUES (:plant_id, 'TEST-M1', 'electricity', 'kWh') RETURNING meter_id"
            ),
            {"plant_id": plant_id},
        ).scalar_one()

        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO silver.readings "
                    "(ts_utc, meter_id, plant_id, value, unit, quality, "
                    " source_system, ingestion_run_id) "
                    "VALUES ('2026-01-01 10:37:00+00', :meter_id, :plant_id, 1.0, "
                    "'kWh', 'ok', 'test', gen_random_uuid())"
                ),
                {"meter_id": meter_id, "plant_id": plant_id},
            )


def test_silver_missing_rows_carry_no_value(migrated_engine: Engine) -> None:
    """A reading flagged `missing` cannot carry a value.

    This constraint is what stops "no data" from being stored as zero, which
    would silently deflate every average computed over the period.
    """
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    with migrated_engine.begin() as connection:
        plant_id = connection.execute(
            text(
                "INSERT INTO reference.plants (code, name, timezone) "
                "VALUES ('TEST-MISS', 'Missing Test Plant', 'UTC') RETURNING plant_id"
            )
        ).scalar_one()
        meter_id = connection.execute(
            text(
                "INSERT INTO reference.meters (plant_id, tag, commodity, unit) "
                "VALUES (:plant_id, 'TEST-M2', 'electricity', 'kWh') RETURNING meter_id"
            ),
            {"plant_id": plant_id},
        ).scalar_one()

        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO silver.readings "
                    "(ts_utc, meter_id, plant_id, value, unit, quality, "
                    " source_system, ingestion_run_id) "
                    "VALUES ('2026-01-01 10:00:00+00', :meter_id, :plant_id, 0.0, "
                    "'kWh', 'missing', 'test', gen_random_uuid())"
                ),
                {"meter_id": meter_id, "plant_id": plant_id},
            )


def test_gold_coverage_is_bounded(migrated_engine: Engine) -> None:
    """A Gold coverage fraction outside [0, 1] is rejected."""
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    with migrated_engine.begin() as connection:
        plant_id = connection.execute(
            text(
                "INSERT INTO reference.plants (code, name, timezone) "
                "VALUES ('TEST-COV', 'Coverage Test Plant', 'UTC') RETURNING plant_id"
            )
        ).scalar_one()

        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO gold.kpi_hourly "
                    "(period_start, plant_id, kpi_key, value, unit, coverage, run_id) "
                    "VALUES ('2026-01-01 00:00:00+00', :plant_id, 'total_energy', "
                    "1.0, 'kWh', 1.5, gen_random_uuid())"
                ),
                {"plant_id": plant_id},
            )


def test_silver_live_uniqueness_allows_superseded_rows(migrated_engine: Engine) -> None:
    """One live row per (meter, interval), but superseded corrections may coexist.

    This is what lets a corrected reading replace its predecessor while the
    original remains for audit.
    """
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    with migrated_engine.begin() as connection:
        plant_id = connection.execute(
            text(
                "INSERT INTO reference.plants (code, name, timezone) "
                "VALUES ('TEST-UQ', 'Uniqueness Test Plant', 'UTC') RETURNING plant_id"
            )
        ).scalar_one()
        meter_id = connection.execute(
            text(
                "INSERT INTO reference.meters (plant_id, tag, commodity, unit) "
                "VALUES (:plant_id, 'TEST-M3', 'electricity', 'kWh') RETURNING meter_id"
            ),
            {"plant_id": plant_id},
        ).scalar_one()

        insert = text(
            "INSERT INTO silver.readings "
            "(ts_utc, meter_id, plant_id, value, unit, quality, source_system, "
            " ingestion_run_id, superseded_at) "
            "VALUES ('2026-01-01 00:00:00+00', :meter_id, :plant_id, :value, 'kWh', "
            "'ok', 'test', gen_random_uuid(), :superseded)"
        )
        params = {"meter_id": meter_id, "plant_id": plant_id}

        # A superseded row plus a live row for the same interval is allowed.
        connection.execute(insert, {**params, "value": 1.0, "superseded": "2026-01-02 00:00:00+00"})
        connection.execute(insert, {**params, "value": 2.0, "superseded": None})

        # A second live row for the same interval is not.
        with pytest.raises(IntegrityError):
            connection.execute(insert, {**params, "value": 3.0, "superseded": None})


def test_downgrade_reverts_cleanly(database_url: str) -> None:
    """The full chain downgrades back to base without error.

    A downgrade path that has never been exercised is discovered during an
    incident, which is the worst possible moment to find out it does not work.
    """
    import os  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url

    root = Path(__file__).resolve().parent.parent.parent
    # `toml_file=` rather than the positional `file_`: the positional argument is
    # parsed as INI, and pyproject.toml's repeated [[tool.mypy.overrides]] tables
    # are valid TOML but a duplicate section to configparser.
    config = Config(toml_file=str(root / "pyproject.toml"))
    config.set_main_option("script_location", str(root / "migrations"))

    engine = create_engine(database_url, future=True)
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "base")

        inspector = inspect(engine)
        remaining = set(inspector.get_schema_names()) & set(ALL_SCHEMAS)
        assert not remaining, f"schemas left behind after downgrade: {sorted(remaining)}"
    finally:
        with engine.begin() as connection:
            for schema in reversed(ALL_SCHEMAS):
                connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
        engine.dispose()
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
