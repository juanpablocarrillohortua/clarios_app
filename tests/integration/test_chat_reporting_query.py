"""The chat assistant's queries on the real reporting views, with the reporting login.

A database at head (TimescaleDB container) and the login ``energy reporting
create-reader`` makes. The views are empty here: what is tested is what the
login and the adapter let a query do, and that the simulated views have the
real ones' columns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from energy.adapters.postgres_reporting_query import PostgresReportingQuery
from energy.adapters.simulated_reporting import VIEW_COLUMNS
from energy.core.settings import get_settings
from energy.domain.exceptions import (
    DatabaseUnavailableError,
    MissingRelationError,
    QueryFailedError,
    QueryTimeoutError,
)
from energy.pipelines.flows.reporting_access import create_reader

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

READER = "chat_test_reader"
READER_PASSWORD = "chat-test-password-1"


@pytest.fixture
def query(gold_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[PostgresReportingQuery]:
    """The adapter, connected as a reporting login made by the flow."""
    monkeypatch.setenv("REPORTING_DB_USER", READER)
    monkeypatch.setenv("REPORTING_DB_PASSWORD", READER_PASSWORD)
    get_settings.cache_clear()
    create_reader(engine=gold_engine)
    adapter = PostgresReportingQuery(gold_engine.url.set(username=READER, password=READER_PASSWORD))
    try:
        yield adapter
    finally:
        adapter.dispose()
        get_settings.cache_clear()
        with gold_engine.begin() as connection:
            # The name is this module's own constant.
            connection.execute(text(f"DROP ROLE IF EXISTS {READER}"))


class TestPostgresReportingQuery:
    def test_reads_from_the_database(self, query: PostgresReportingQuery) -> None:
        assert query.data_source == "database"

    def test_the_simulation_has_the_real_views_and_columns(
        self, query: PostgresReportingQuery
    ) -> None:
        described = query.describe()

        assert set(described) == set(VIEW_COLUMNS)
        for view, columns in VIEW_COLUMNS.items():
            assert [name for name, _ in described[view]] == [name for name, _ in columns], view

    def test_runs_a_select_on_a_view(self, query: PostgresReportingQuery) -> None:
        result = query.run(
            "SELECT count(*) AS months FROM equs_batt_mensual", max_rows=5, timeout_seconds=10
        )

        assert result.columns == ("months",)
        assert result.rows == ((0,),)
        assert not result.truncated

    def test_sends_the_sql_as_written(self, query: PostgresReportingQuery) -> None:
        result = query.run("SELECT '100%' AS share", max_rows=5, timeout_seconds=10)

        assert result.rows == (("100%",),)

    def test_says_when_there_were_more_rows(self, query: PostgresReportingQuery) -> None:
        result = query.run("SELECT generate_series(1, 10) AS n", max_rows=3, timeout_seconds=10)

        assert result.rows == ((1,), (2,), (3,))
        assert result.truncated

    def test_a_missing_table_is_missing(self, query: PostgresReportingQuery) -> None:
        with pytest.raises(MissingRelationError):
            query.run("SELECT * FROM energia_orec", max_rows=5, timeout_seconds=10)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM bronze.ingestion_runs",
            "SELECT * FROM silver.factores_equ",
            "SELECT * FROM gold.cierre_mes",
        ],
    )
    def test_reads_nothing_outside_reporting(self, query: PostgresReportingQuery, sql: str) -> None:
        with pytest.raises(QueryFailedError) as failed:
            query.run(sql, max_rows=5, timeout_seconds=10)

        assert "permission denied" in failed.value.details["reason"]

    def test_writes_nothing(self, query: PostgresReportingQuery) -> None:
        with pytest.raises(QueryFailedError) as failed:
            query.run("CREATE TABLE reporting.kept AS SELECT 1", max_rows=5, timeout_seconds=10)

        assert "read-only" in failed.value.details["reason"]

    def test_stops_a_query_at_its_time_limit(self, query: PostgresReportingQuery) -> None:
        with pytest.raises(QueryTimeoutError):
            query.run("SELECT pg_sleep(5)", max_rows=5, timeout_seconds=0.3)

    def test_the_connection_is_usable_after_a_failure(self, query: PostgresReportingQuery) -> None:
        with pytest.raises(QueryTimeoutError):
            query.run("SELECT pg_sleep(5)", max_rows=5, timeout_seconds=0.3)

        assert query.run("SELECT 1", max_rows=1, timeout_seconds=10).rows == ((1,),)


def test_an_unreachable_database_is_said(gold_engine: Engine) -> None:
    adapter = PostgresReportingQuery(
        gold_engine.url.set(host="127.0.0.1", port=1, username=READER, password=READER_PASSWORD)
    )
    try:
        with pytest.raises(DatabaseUnavailableError):
            adapter.describe()
        with pytest.raises(DatabaseUnavailableError):
            adapter.run("SELECT 1", max_rows=1, timeout_seconds=5)
    finally:
        adapter.dispose()
