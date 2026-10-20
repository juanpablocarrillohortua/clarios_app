"""The chat assistant's data tools without a network or a database server.

The simulated views run in a real, in-memory DuckDB; the PostgreSQL adapter's
own test is ``tests/integration/test_chat_reporting_query.py``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import Field

from energy.adapters.duckdb_reporting_query import DuckDBReportingQuery
from energy.adapters.langchain_chat_model import LangChainChatModel, load_system_prompt
from energy.adapters.postgres_reporting_query import PostgresReportingQuery
from energy.adapters.simulated_reporting import VIEW_COLUMNS, simulated_views
from energy.adapters.yaml_chat_sources import SOURCES_FILE, load_chat_sources
from energy.core.settings import ChatSettings, Settings
from energy.domain.chat import build_context
from energy.domain.chat_sources import DataSource, SourceCatalogue, SourceState
from energy.domain.exceptions import (
    ChatNotConfiguredError,
    ConfigurationError,
    DatabaseUnavailableError,
    MissingRelationError,
    QueryFailedError,
    QueryTimeoutError,
)
from energy.pipelines.flows.chat import build_reporting_query, reporting_login_url
from energy.ports.reporting_query import QueryResult, ReportingQuery
from energy.services.chat_data_service import PENDING_IMPLEMENTATION, ChatDataService

if TYPE_CHECKING:
    from collections.abc import Mapping

pytestmark = pytest.mark.unit

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
SEPTEMBER = date(2026, 9, 1)
NOW = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)
PROCESS_COLUMNS = {
    "OREC": "equs_orec",
    "ENSAMBLE": "equs_ensamble",
    "FORMACION": "equs_formacion",
    "DECORADO": "equs_decorado",
    "SHIPPING": "equs_shipping",
}
SIMULATED_NOTICE = "(Datos simulados: no son cifras reales de la planta.)"
PENDING_SENTENCE = "No encontrado: la tabla está pendiente de implementación."


@pytest.fixture(scope="module")
def simulation() -> DuckDBReportingQuery:
    """One simulated database for the module: building it takes about a second."""
    return DuckDBReportingQuery()


@pytest.fixture(scope="module")
def catalogue() -> SourceCatalogue:
    return load_chat_sources(CONFIG_DIR)


@pytest.fixture
def service(simulation: DuckDBReportingQuery, catalogue: SourceCatalogue) -> ChatDataService:
    return ChatDataService(simulation, catalogue, row_limit=5, timeout_seconds=5)


def _rows(view: str) -> list[dict[str, Any]]:
    simulated = simulated_views()[view]
    names = [column for column, _ in simulated.columns]
    return [dict(zip(names, row, strict=True)) for row in simulated.rows]


# -----------------------------------------------------------------------------
# The simulated world (energy.adapters.simulated_reporting)
# -----------------------------------------------------------------------------
class TestSimulatedWorld:
    def test_has_the_twelve_views_of_revision_0006(self) -> None:
        assert set(simulated_views()) == {
            "aporte_cuenta",
            "articulos",
            "calidad_datos_mensual",
            "cierres",
            "cierres_calidad",
            "cuentas",
            "equs_batt_mensual",
            "equs_cerrado",
            "equs_cerrado_historial",
            "equs_mensual",
            "equs_poly_mensual",
            "recibos_produccion",
        }

    def test_is_the_same_every_time(self) -> None:
        assert simulated_views() == simulated_views()

    def test_every_row_has_its_columns(self) -> None:
        for name, view in simulated_views().items():
            assert view.rows, name
            assert {len(row) for row in view.rows} == {len(view.columns)}, name

    def test_column_h_is_the_contributions_summed(self) -> None:
        summed: dict[tuple[date, str], Decimal] = defaultdict(Decimal)
        for row in _rows("aporte_cuenta"):
            summed[row["mes"], row["cuenta_stats"]] += row["equs"]

        for row in _rows("equs_mensual"):
            assert row["equs"] == summed.get((row["mes"], row["cuenta_stats"]), Decimal(0))

    def test_every_driver_has_a_row_every_month(self) -> None:
        rows = _rows("equs_mensual")

        assert len(rows) == 12 * len(_rows("cuentas"))

    def test_equs_batt_is_column_h_by_process(self) -> None:
        by_process: dict[tuple[date, str], Decimal] = defaultdict(Decimal)
        for row in _rows("equs_mensual"):
            if row["suma_eq"]:
                by_process[row["mes"], row["proceso"]] += row["equs"]

        for month in _rows("equs_batt_mensual"):
            processes = [month[column] for column in PROCESS_COLUMNS.values()]
            assert month["equs_batt"] == sum(processes, Decimal(0))
            for process, column in PROCESS_COLUMNS.items():
                assert month[column] == by_process[month["mes"], process]

    def test_equs_is_quantity_times_factor(self) -> None:
        for row in _rows("equs_mensual"):
            assert abs(row["equs"] - row["cantidad"] * row["factor"]) < Decimal("0.01")

    def test_closed_months_equal_column_h_and_september_is_open(self) -> None:
        mensual = {(row["mes"], row["cuenta_stats"]): row["equs"] for row in _rows("equs_mensual")}
        cerrado = _rows("equs_cerrado")

        assert {row["mes"] for row in cerrado} == {
            month for month, _ in mensual if month != SEPTEMBER
        }
        for row in cerrado:
            assert row["equs"] == mensual[row["mes"], row["cuenta_stats"]]

    def test_one_reclose_keeps_what_it_replaced(self) -> None:
        cierres = _rows("cierres")
        recloses = [row for row in cierres if row["recierre"]]

        assert len(recloses) == 1
        (reclose,) = recloses
        assert {row["cierre_id"] for row in _rows("equs_cerrado_historial")} == {
            reclose["cierre_id"]
        }
        assert sum(row["vigente"] for row in cierres) == 11  # one per closed month

    def test_poly_by_month_sums_its_receipts(self) -> None:
        pieces: dict[tuple[date, str], Decimal] = defaultdict(Decimal)
        for row in _rows("recibos_produccion"):
            if row["cuenta_poly"] is not None:
                pieces[row["mes"], row["cuenta_poly"]] += row["cantidad"]

        for row in _rows("equs_poly_mensual"):
            assert row["piezas"] == pieces[row["mes"], row["cuenta"]]

    def test_items_are_invented(self) -> None:
        assert all(row["numero_articulo"].startswith("SIM-") for row in _rows("articulos"))


# -----------------------------------------------------------------------------
# The DuckDB adapter (energy.adapters.duckdb_reporting_query)
# -----------------------------------------------------------------------------
class TestDuckDBReportingQuery:
    def test_is_simulated_and_a_reporting_query(self, simulation: DuckDBReportingQuery) -> None:
        assert simulation.data_source == "simulated"
        assert isinstance(simulation, ReportingQuery)

    def test_describes_every_view_in_the_order_of_its_columns(
        self, simulation: DuckDBReportingQuery
    ) -> None:
        described = simulation.describe()

        assert set(described) == set(VIEW_COLUMNS)
        for view, columns in VIEW_COLUMNS.items():
            assert [name for name, _ in described[view]] == [name for name, _ in columns]

    def test_runs_postgresql_style_sql(self, simulation: DuckDBReportingQuery) -> None:
        result = simulation.run(
            "SELECT date_trunc('month', fecha_efectiva)::date AS mes, "
            "sum(equs) FILTER (WHERE suma_eq) AS eq "
            "FROM reporting.aporte_cuenta WHERE origen ILIKE 'RECIBO' "
            "GROUP BY 1 ORDER BY 1",
            max_rows=50,
            timeout_seconds=5,
        )

        assert result.columns == ("mes", "eq")
        assert len(result.rows) == 12
        assert not result.truncated

    def test_reads_the_views_without_the_schema(self, simulation: DuckDBReportingQuery) -> None:
        result = simulation.run(
            "SELECT equs_batt FROM equs_batt_mensual WHERE mes = DATE '2026-09-01'",
            max_rows=5,
            timeout_seconds=5,
        )

        (september,) = (row for row in _rows("equs_batt_mensual") if row["mes"] == SEPTEMBER)
        assert result.rows == ((september["equs_batt"],),)

    def test_says_when_there_were_more_rows(self, simulation: DuckDBReportingQuery) -> None:
        result = simulation.run("SELECT * FROM cuentas", max_rows=3, timeout_seconds=5)

        assert len(result.rows) == 3
        assert result.truncated

    def test_a_missing_table_is_missing(self, simulation: DuckDBReportingQuery) -> None:
        with pytest.raises(MissingRelationError):
            simulation.run("SELECT * FROM energia_mensual", max_rows=5, timeout_seconds=5)

    def test_a_wrong_column_is_the_querys_mistake(self, simulation: DuckDBReportingQuery) -> None:
        with pytest.raises(QueryFailedError) as failed:
            simulation.run("SELECT kwh FROM cuentas", max_rows=5, timeout_seconds=5)

        assert "kwh" in failed.value.details["reason"]

    def test_reads_no_file(self, simulation: DuckDBReportingQuery) -> None:
        with pytest.raises(QueryFailedError) as failed:
            simulation.run(
                "SELECT * FROM read_csv('pyproject.toml')", max_rows=5, timeout_seconds=5
            )

        assert "disabled" in failed.value.details["reason"]

    def test_stops_a_query_at_its_time_limit(self, simulation: DuckDBReportingQuery) -> None:
        with pytest.raises(QueryTimeoutError):
            simulation.run(
                "SELECT count(*) FROM range(100000000) AS a, range(1000) AS b",
                max_rows=5,
                timeout_seconds=0.3,
            )

    def test_keeps_nothing_a_query_changes(self, simulation: DuckDBReportingQuery) -> None:
        before = simulation.run("SELECT count(*) FROM cuentas", max_rows=1, timeout_seconds=5)

        # The check would refuse these; the adapter must not keep them either.
        simulation.run("DELETE FROM reporting.cuentas", max_rows=1, timeout_seconds=5)
        simulation.run("CREATE TABLE reporting.kept AS SELECT 1", max_rows=1, timeout_seconds=5)

        after = simulation.run("SELECT count(*) FROM cuentas", max_rows=1, timeout_seconds=5)
        assert after.rows == before.rows
        with pytest.raises(MissingRelationError):
            simulation.run("SELECT * FROM reporting.kept", max_rows=1, timeout_seconds=5)

    def test_cannot_undo_its_settings(self, simulation: DuckDBReportingQuery) -> None:
        # The check would refuse it; the locked configuration refuses it too.
        with pytest.raises(QueryFailedError) as failed:
            simulation.run("SET enable_external_access = true", max_rows=1, timeout_seconds=5)

        assert "configuration" in failed.value.details["reason"]


# -----------------------------------------------------------------------------
# The catalogue (config/chat/sources.yaml)
# -----------------------------------------------------------------------------
class TestSourceCatalogue:
    def test_every_view_it_names_is_simulated(self, catalogue: SourceCatalogue) -> None:
        assert catalogue.view_names == set(VIEW_COLUMNS)

    def test_the_energy_files_are_pending(self, catalogue: SourceCatalogue) -> None:
        pending = {
            name for source in catalogue.by_state(SourceState.PENDING) for name in source.files
        }

        assert pending == {
            "Base_de_datos.xlsx",
            "SCADA-Base de datos F&A.xlsx",
            "SCADA-Base de datos Energia RC.xlsx",
            "SCADA-Base de datos GAS RC.xlsx",
        }

    def test_refuses_an_implemented_source_without_views(self) -> None:
        with pytest.raises(ConfigurationError) as refused:
            SourceCatalogue((DataSource("a", ("a.xlsx",), "A", SourceState.IMPLEMENTED),))

        assert "names its views" in str(refused.value.details["problems"])

    def test_refuses_a_repeated_key(self) -> None:
        source = DataSource("a", ("a.xlsx",), "A", SourceState.PENDING)

        with pytest.raises(ConfigurationError):
            SourceCatalogue((source, source))

    @pytest.mark.parametrize(
        "content",
        [
            "sources: []",
            "sources:\n  - key: a\n    description: A\n    state: maybe\n",
            "sources:\n  - key: a\n    state: pending\n",
            "not: [valid",
        ],
    )
    def test_refuses_a_malformed_file(self, tmp_path: Path, content: str) -> None:
        target = tmp_path / SOURCES_FILE
        target.parent.mkdir(parents=True)
        target.write_text(content, encoding="utf-8")

        with pytest.raises(ConfigurationError):
            load_chat_sources(tmp_path)

    def test_refuses_a_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="cannot be read"):
            load_chat_sources(tmp_path)


# -----------------------------------------------------------------------------
# The tools (energy.services.chat_data_service)
# -----------------------------------------------------------------------------
class _Unreachable:
    """A database that cannot be reached."""

    data_source = "database"

    def describe(self) -> Mapping[str, tuple[tuple[str, str], ...]]:
        msg = "the reporting views cannot be read"
        raise DatabaseUnavailableError(msg)

    def run(self, sql: str, *, max_rows: int, timeout_seconds: float) -> QueryResult:  # noqa: ARG002
        msg = "the reporting views cannot be read"
        raise DatabaseUnavailableError(msg)


class TestChatDataService:
    def test_lists_the_sources_and_where_the_data_comes_from(
        self, service: ChatDataService
    ) -> None:
        answer = json.loads(service.list_sources())

        assert answer["data_source"] == "simulated"
        states = {source["key"]: source["state"] for source in answer["sources"]}
        assert states["equs_exports"] == "implemented"
        assert states["energy_by_area"] == "pending"

    def test_describes_the_views(self, service: ChatDataService) -> None:
        answer = json.loads(service.describe_reporting())

        assert answer["schema"] == "reporting"
        columns = [column["column"] for column in answer["views"]["equs_batt_mensual"]]
        assert columns[:3] == ["mes", "anio_fiscal", "equs_orec"]

    def test_runs_a_select_and_answers_text_values(self, service: ChatDataService) -> None:
        answer = json.loads(
            service.run_sql("SELECT mes, equs_batt FROM equs_batt_mensual ORDER BY mes;")
        )

        assert answer["status"] == "ok"
        assert answer["data_source"] == "simulated"
        assert answer["columns"] == ["mes", "equs_batt"]
        assert answer["rows"][0][0] == "2025-10-01"
        assert answer["truncated"] is True  # 12 months, 5 rows at most

    def test_a_missing_table_is_pending(self, service: ChatDataService) -> None:
        answer = json.loads(service.run_sql("SELECT sum(kwh) FROM energia_orec"))

        assert answer["status"] == PENDING_IMPLEMENTATION

    @pytest.mark.parametrize(
        ("query", "reason"),
        [
            ("DELETE FROM cuentas", "only a SELECT"),
            ("SELECT 1; SELECT 2", "one statement"),
            ("SELECT kwh FROM cuentas", "kwh"),
        ],
    )
    def test_a_refusal_is_a_reason_for_the_model(
        self, service: ChatDataService, query: str, reason: str
    ) -> None:
        answer = json.loads(service.run_sql(query))

        assert answer["status"] == "error"
        assert reason in answer["reason"]

    def test_a_slow_query_asks_for_less(
        self, simulation: DuckDBReportingQuery, catalogue: SourceCatalogue
    ) -> None:
        slow = ChatDataService(simulation, catalogue, row_limit=5, timeout_seconds=0.3)

        answer = json.loads(slow.run_sql("SELECT count(*) FROM range(100000000), range(1000)"))

        assert answer == {
            "status": "error",
            "reason": "the query took too long; aggregate or filter more",
        }

    def test_an_unreachable_database_is_said(self, catalogue: SourceCatalogue) -> None:
        down = ChatDataService(_Unreachable(), catalogue, row_limit=5, timeout_seconds=5)

        assert json.loads(down.run_sql("SELECT 1"))["reason"] == "the database cannot be reached"
        assert json.loads(down.describe_reporting())["status"] == "error"

    def test_the_sources_note_says_what_is_pending(self, service: ChatDataService) -> None:
        note = service.sources_note()

        assert note.startswith("Data sources of the plant (data: simulated):")
        assert "pending (no table yet)" in note
        assert "Base_de_datos.xlsx" in note
        assert "Views: cuentas, articulos, equs_mensual" in note

    def test_the_tools_tell_the_model_when_to_use_them(self, service: ChatDataService) -> None:
        for tool in (service.list_sources, service.describe_reporting, service.run_sql):
            assert tool.__doc__
            assert len(tool.__doc__.strip()) > 40


# -----------------------------------------------------------------------------
# The tool loop (energy.adapters.langchain_chat_model)
# -----------------------------------------------------------------------------
class ToolCallingModel(GenericFakeChatModel):
    """A fake chat model that may call tools, and keeps what it was sent."""

    seen: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:  # noqa: ARG002 (calls them all)
        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        self.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def test_the_model_runs_a_query_and_reads_its_rows(service: ChatDataService) -> None:
    query = "SELECT equs_batt FROM equs_batt_mensual WHERE mes = DATE '2026-09-01'"
    llm = ToolCallingModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "run_sql", "args": {"query": query}, "id": "call-1"}],
                ),
                AIMessage(content=f"Septiembre tuvo X EQ. {SIMULATED_NOTICE}"),
            ]
        )
    )
    model = LangChainChatModel(
        llm, "Be brief.", (service.list_sources, service.describe_reporting, service.run_sql)
    )

    answer = model.reply(build_context([], "¿Cuántas EQ en septiembre?", now=NOW, limit=20))

    assert answer.endswith(SIMULATED_NOTICE)
    second_call = llm.seen[1]
    (tool_message,) = (message for message in second_call if isinstance(message, ToolMessage))
    result = json.loads(str(tool_message.content))
    (september,) = (row for row in _rows("equs_batt_mensual") if row["mes"] == SEPTEMBER)
    assert result["data_source"] == "simulated"
    assert Decimal(result["rows"][0][0]) == september["equs_batt"]


def test_the_prompt_quotes_the_two_sentences_word_for_word() -> None:
    prompt = load_system_prompt()

    assert PENDING_SENTENCE in prompt
    assert SIMULATED_NOTICE in prompt
    for tool in ("list_sources", "describe_reporting", "run_sql"):
        assert tool in prompt


# -----------------------------------------------------------------------------
# The switch (CHAT_DATA_SOURCE) and the reporting login
# -----------------------------------------------------------------------------
@pytest.fixture
def database_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings for database mode, with made-up values only."""
    monkeypatch.setenv("CHAT_DATA_SOURCE", "database")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://energy:app-secret@db:5432/energy")
    monkeypatch.setenv("REPORTING_DB_USER", "powerbi")
    monkeypatch.setenv("REPORTING_DB_PASSWORD", "made-up-password")
    for name in ("CHAT_DB_HOST", "CHAT_DB_PORT"):
        monkeypatch.delenv(name, raising=False)


class TestDataSourceSwitch:
    def test_is_simulated_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CHAT_DATA_SOURCE", raising=False)

        assert ChatSettings(_env_file=None).data_source == "simulated"

    def test_refuses_an_unknown_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CHAT_DATA_SOURCE", "excel")

        with pytest.raises(ValueError, match=r"CHAT_DATA_SOURCE|data_source"):
            ChatSettings(_env_file=None)

    def test_simulated_needs_no_database(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CHAT_DATA_SOURCE", " Simulated ")

        query = build_reporting_query(Settings(chat=ChatSettings(_env_file=None)))

        assert isinstance(query, DuckDBReportingQuery)

    @pytest.mark.usefixtures("database_mode")
    def test_database_mode_uses_the_reporting_login_not_the_applications(self) -> None:
        url = reporting_login_url(Settings(chat=ChatSettings(_env_file=None)))

        assert url.username == "powerbi"
        assert url.password == "made-up-password"
        assert (url.host, url.port, url.database) == ("db", 5432, "energy")
        assert "app-secret" not in url.render_as_string(hide_password=False)

    @pytest.mark.usefixtures("database_mode")
    def test_database_mode_can_go_through_the_bridge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CHAT_DB_HOST", "127.0.0.1")
        monkeypatch.setenv("CHAT_DB_PORT", "5433")

        url = reporting_login_url(Settings(chat=ChatSettings(_env_file=None)))

        assert (url.host, url.port) == ("127.0.0.1", 5433)

    @pytest.mark.usefixtures("database_mode")
    def test_database_mode_builds_the_postgresql_adapter_without_connecting(self) -> None:
        query = build_reporting_query(Settings(chat=ChatSettings(_env_file=None)))

        assert isinstance(query, PostgresReportingQuery)
        assert query.data_source == "database"
        query.dispose()

    @pytest.mark.usefixtures("database_mode")
    def test_database_mode_needs_the_reporting_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REPORTING_DB_PASSWORD", "")

        with pytest.raises(ChatNotConfiguredError, match="REPORTING_DB_PASSWORD"):
            build_reporting_query(Settings(chat=ChatSettings(_env_file=None)))
