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
    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

EXPECTED_TABLES: dict[str, set[str]] = {
    "reference": {"plants", "meters", "shifts", "targets"},
    BRONZE_SCHEMA: {"raw_readings", "ingestion_runs", "raw_files"},
    SILVER_SCHEMA: {"readings", "quarantine", "consolidation_audit"},
    GOLD_SCHEMA: {"kpi_hourly", "kpi_daily", "plant_daily_summary"},
}

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
