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

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, inspect, text

from energy.core.constants import ALL_SCHEMAS, BRONZE_SCHEMA, GOLD_SCHEMA, SILVER_SCHEMA

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

EXPECTED_TABLES: dict[str, set[str]] = {
    "reference": {"plants", "meters", "shifts", "targets"},
    BRONZE_SCHEMA: {"raw_readings", "ingestion_runs", "raw_files", *LANDING_TABLES},
    SILVER_SCHEMA: {"readings", "quarantine", "consolidation_audit"},
    GOLD_SCHEMA: {"kpi_hourly", "kpi_daily", "plant_daily_summary"},
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
    assert version == "0005"


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
