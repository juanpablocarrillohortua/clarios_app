"""Tests for the command-line entry point.

Layer: tests (unit)

The commands are thin: they parse arguments, call the load functions and print.
So these tests replace the load functions and check the three things the CLI
owns - how arguments are turned into a request, what is printed, and the exit
code a shell or a scheduler will see.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from typer.testing import CliRunner

import energy.__main__ as cli
from energy.domain.equs_silver import LineWithoutPlant, SilverBuildReport, StructureSheet
from energy.domain.exceptions import (
    CloseRefusedError,
    LockTimeoutError,
    PipelineError,
    RepositoryError,
)
from energy.domain.landing import SheetSpec
from energy.pipelines.flows.equs_bronze_load import ROUTES, LoadAllResult, RouteOutcome
from energy.services.bronze_loader import LoadResult, SheetOutcome

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

runner = CliRunner()


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    """Return the path of a file that exists; its content is never read here."""
    path = tmp_path / "EQUS BATT.xlsx"
    path.write_bytes(b"x")
    return path


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the wiring and the load functions, recording how they are called."""
    recorded: dict[str, Any] = {}

    def fake_build_loader(archive_dir: Path | None = None) -> str:
        recorded["archive_dir"] = archive_dir
        return "loader"

    def fake_load_workbook(
        loader: str, source_key: str, path: Path, sheets: Any, **options: Any
    ) -> LoadResult:
        recorded.update(loader=loader, source_key=source_key, path=path, sheets=sheets, **options)
        if recorded.get("raise"):
            raise recorded["raise"]
        window_start, window_end = recorded.get("period", (None, None))
        return LoadResult(
            source_key=source_key,
            filename=path.name,
            content_hash="0" * 64,
            run_id=recorded.get("run_id", uuid4()),
            sheets=tuple(
                SheetOutcome(
                    s.name, s.dataset_key, 1_753, skipped=recorded.get("run_id", 1) is None
                )
                for s in sheets
            ),
            window_start=window_start,
            window_end=window_end,
        )

    def fake_load_all(loader: str, directory: Path, **options: Any) -> LoadAllResult:
        recorded.update(loader=loader, directory=directory, **options)
        outcomes = recorded.get("outcomes", ())
        for outcome in outcomes:
            options["on_outcome"](outcome)
        return LoadAllResult(tuple(outcomes), seconds=1.5, unmatched=recorded.get("unmatched", ()))

    monkeypatch.setattr(cli, "build_loader", fake_build_loader)
    monkeypatch.setattr(cli, "load_workbook", fake_load_workbook)
    monkeypatch.setattr(cli, "load_all", fake_load_all)
    return recorded


def test_help_lists_the_equs_commands() -> None:
    """The two loads are discoverable from the shell."""
    result = runner.invoke(cli.build_app(), ["equs", "--help"])

    assert result.exit_code == 0
    assert "load-all" in result.output
    assert "load " in result.output


def test_load_parses_sheets_and_options(
    calls: dict[str, Any], workbook: Path, tmp_path: Path
) -> None:
    """Sheets, the header row, the window and the archive directory reach the loader."""
    result = runner.invoke(
        cli.build_app(),
        [
            "equs",
            "load",
            "manual_sheets",
            str(workbook),
            "--sheet",
            "Clasif_Poly=clasif_poly",
            "-s",
            "L.PRODUCCION=l_produccion:3",
            "--header-row",
            "0",
            "--plant-code",
            "1010",
            "--window-start",
            "2025-10-01",
            "--window-end",
            "2026-09-30",
            "--force",
            "--archive-dir",
            str(tmp_path / "archive"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls["source_key"] == "manual_sheets"
    assert calls["path"] == workbook
    # The invocation's header row applies to the sheet that names none.
    assert calls["sheets"] == [
        SheetSpec("Clasif_Poly", "clasif_poly", 0),
        SheetSpec("L.PRODUCCION", "l_produccion", 3),
    ]
    assert calls["plant_code"] == "1010"
    assert (calls["window_start"], calls["window_end"]) == (date(2025, 10, 1), date(2026, 9, 30))
    assert calls["force"] is True
    assert calls["archive_dir"] == tmp_path / "archive"
    assert "Clasif_Poly=clasif_poly" in result.output
    assert "1,753 rows" in result.output
    assert "Landed 3,506 rows" in result.output


def test_load_without_sheets_asks_for_the_defaults(calls: dict[str, Any], workbook: Path) -> None:
    """Naming no sheet leaves the choice to the source's defaults."""
    result = runner.invoke(cli.build_app(), ["equs", "load", "qad_rct_wo", str(workbook)])

    assert result.exit_code == 0, result.output
    assert calls["sheets"] == []
    assert (calls["window_start"], calls["window_end"]) == (None, None)
    assert calls["force"] is False
    assert calls["archive_dir"] is None
    # The fake recorded no period, so none is printed.
    assert "period" not in result.output


def test_load_prints_the_period_the_run_covers(calls: dict[str, Any], workbook: Path) -> None:
    """Whatever years the rows held, the command shows them."""
    calls["period"] = (date(2023, 10, 2), date(2024, 9, 30))

    result = runner.invoke(
        cli.build_app(), ["equs", "load", "qad_rct_wo", str(workbook), "--sheet", "Datos"]
    )

    assert result.exit_code == 0, result.output
    assert "2023-10-02 to 2024-09-30" in result.output


def test_load_prints_a_period_with_one_open_end(calls: dict[str, Any], workbook: Path) -> None:
    """A caller may state a single date; the other end is shown as unknown."""
    calls["period"] = (date(2026, 1, 1), None)

    result = runner.invoke(
        cli.build_app(), ["equs", "load", "qad_rct_wo", str(workbook), "--sheet", "Datos"]
    )

    assert "2026-01-01 to ?" in result.output


def test_load_says_when_there_was_nothing_to_do(calls: dict[str, Any], workbook: Path) -> None:
    """An already-landed load exits 0 and says so, as idempotent commands should."""
    calls["run_id"] = None

    result = runner.invoke(
        cli.build_app(), ["equs", "load", "qad_rct_wo", str(workbook), "--sheet", "Datos"]
    )

    assert result.exit_code == 0, result.output
    assert "already landed, skipped" in result.output
    assert "Nothing to do" in result.output


def test_load_exits_non_zero_when_the_load_fails(calls: dict[str, Any], workbook: Path) -> None:
    """A failed load must be visible to whatever invoked the command."""
    calls["raise"] = PipelineError("load failed and nothing was landed", run_id="abc")

    result = runner.invoke(cli.build_app(), ["equs", "load", "qad_rct_wo", str(workbook)])

    assert result.exit_code == 1
    assert "nothing was landed" in result.output


def test_load_rejects_a_malformed_sheet_before_loading(
    calls: dict[str, Any], workbook: Path
) -> None:
    """A sheet argument that cannot be read stops the command with exit code 1."""
    result = runner.invoke(
        cli.build_app(), ["equs", "load", "qad_rct_wo", str(workbook), "--sheet", "Datos:x"]
    )

    assert result.exit_code == 1
    assert "source_key" not in calls


def test_load_requires_an_existing_file(calls: dict[str, Any], tmp_path: Path) -> None:
    """A path that does not exist is a usage error, caught by the parser."""
    result = runner.invoke(
        cli.build_app(), ["equs", "load", "qad_rct_wo", str(tmp_path / "missing.xlsx")]
    )

    assert result.exit_code == 2
    assert "source_key" not in calls


def test_load_all_assumes_no_year(calls: dict[str, Any], tmp_path: Path) -> None:
    """load-all passes no period: each run takes it from the rows it lands."""
    result = runner.invoke(cli.build_app(), ["equs", "load-all", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert calls["directory"] == tmp_path
    assert "window" not in calls
    assert calls["include_optional"] is False
    assert calls["force"] is False
    assert "Landed 0 rows" in result.output
    assert "Not loaded" not in result.output


def test_load_all_takes_no_period_option(calls: dict[str, Any], tmp_path: Path) -> None:
    """Several exports may be loaded at once, so one period for all means nothing."""
    result = runner.invoke(
        cli.build_app(),
        ["equs", "load-all", "--dir", str(tmp_path), "--window-start", "2025-10-01"],
    )

    assert result.exit_code == 2
    assert "loader" not in calls


def test_load_all_prints_each_period_and_what_it_left_out(
    calls: dict[str, Any], tmp_path: Path
) -> None:
    """Two exports of one route show their own periods; a stray workbook is named."""

    def landed(filename: str, start: date, end: date) -> RouteOutcome:
        result = LoadResult(
            source_key="qad_rct_wo",
            filename=filename,
            content_hash="0" * 64,
            run_id=uuid4(),
            sheets=(SheetOutcome("Datos", None, 10, skipped=False),),
            window_start=start,
            window_end=end,
        )
        return RouteOutcome(ROUTES[0], "landed", path=tmp_path / filename, result=result)

    calls["outcomes"] = (
        landed("Explorar transacciones por art.xlsx", date(2025, 10, 1), date(2026, 9, 30)),
        landed("Explorar transacciones por art FY24.xlsx", date(2023, 10, 2), date(2024, 9, 30)),
    )
    calls["unmatched"] = (tmp_path / "copia de seguridad.xlsx",)

    result = runner.invoke(cli.build_app(), ["equs", "load-all", "--dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Explorar transacciones por art FY24.xlsx" in result.output
    assert "2025-10-01 to 2026-09-30" in result.output
    assert "2023-10-02 to 2024-09-30" in result.output
    assert "Loads: 2 landed" in result.output
    assert "Not loaded, no route takes them: copia de seguridad.xlsx" in result.output


def test_load_all_prints_each_route_and_fails_on_a_missing_workbook(
    calls: dict[str, Any], tmp_path: Path
) -> None:
    """A required workbook that is absent is reported and turns the exit code to 1."""
    landed = LoadResult(
        source_key="qad_facturas",
        filename="Unidades facturadas.xlsx",
        content_hash="0" * 64,
        run_id=uuid4(),
        sheets=(SheetOutcome("Datos", None, 14_458, skipped=False),),
    )
    calls["outcomes"] = (
        RouteOutcome(ROUTES[0], "missing"),
        RouteOutcome(ROUTES[1], "landed", path=tmp_path / landed.filename, result=landed),
        RouteOutcome(ROUTES[2], "failed", path=tmp_path / "BOM.xlsx", error="broken workbook"),
        RouteOutcome(ROUTES[-1], "missing"),
    )

    result = runner.invoke(cli.build_app(), ["equs", "load-all", "--dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "MISSING" in result.output
    assert "14,458 rows" in result.output
    assert "FAILED: broken workbook" in result.output
    # The item master is expected to be absent for now.
    assert "not delivered yet" in result.output
    assert "Landed 14,458 rows" in result.output
    assert "Missing workbooks: Calculo de equivalente - Explorar transacciones por art.xlsx" in (
        result.output
    )


def test_load_all_refuses_a_directory_that_does_not_exist(
    calls: dict[str, Any], tmp_path: Path
) -> None:
    """Pointing load-all at nothing is an error, not an empty success."""
    result = runner.invoke(cli.build_app(), ["equs", "load-all", "--dir", str(tmp_path / "nope")])

    assert result.exit_code == 1
    assert "loader" not in calls


# -----------------------------------------------------------------------------
# energy equs build-silver
# -----------------------------------------------------------------------------
def _silver_report() -> SilverBuildReport:
    """Return a small report with one finding to print."""
    return SilverBuildReport(
        rows={"reporte_qad": 138_773, "td_form": 1_248},
        structure=(StructureSheet("PLACAS", 220, 1, 1, 7, 0, 0, 211),),
        receipts_left_out=0,
        invoice_lines_left_out=0,
        placeholders={"item_master": 0},
        items_without_description=(),
        lines_without_plant=tuple(LineWithoutPlant(f"LINEA{n:02d}", n) for n in range(1, 13)),
        poly_factor_mismatches=(),
        poly_repeated_items=(),
        formed_without_paste=(),
        decorated_without_pieces=(),
        na_with_poly_category=(),
        seconds=25.6,
    )


def test_help_lists_the_silver_build() -> None:
    """The build is discoverable beside the two loads."""
    result = runner.invoke(cli.build_app(), ["equs", "--help"])

    assert result.exit_code == 0
    assert "build-silver" in result.output


def test_build_silver_prints_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """The command builds once and prints what the build found."""
    builds: list[int] = []

    def fake_build_silver() -> SilverBuildReport:
        builds.append(1)
        return _silver_report()

    monkeypatch.setattr(cli, "build_silver", fake_build_silver)

    result = runner.invoke(cli.build_app(), ["equs", "build-silver"])

    assert result.exit_code == 0, result.output
    assert builds == [1]
    assert "Rows per Silver table" in result.output
    assert "reporte_qad              138,773" in result.output
    assert "PLACAS" in result.output
    assert "Production lines without plant area: 12" in result.output
    # Ten examples by default, then the rest as a count.
    assert "and 2 more" in result.output
    assert "Silver built in 25.6 s." in result.output


def test_build_silver_lists_as_many_examples_as_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--examples` sets how much of each finding is spelled out."""
    monkeypatch.setattr(cli, "build_silver", _silver_report)

    result = runner.invoke(cli.build_app(), ["equs", "build-silver", "--examples", "2"])

    assert result.exit_code == 0, result.output
    assert "LINEA01 (1 receipts), LINEA02 (2 receipts), and 10 more" in result.output


def test_build_silver_exits_non_zero_when_it_cannot_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build that was refused or failed must be visible to whatever ran it."""

    def refuse() -> SilverBuildReport:
        raise PipelineError("Bronze holds no completed load")

    monkeypatch.setattr(cli, "build_silver", refuse)

    result = runner.invoke(cli.build_app(), ["equs", "build-silver"])

    assert result.exit_code == 1
    assert "no completed load" in result.output
    assert "Silver built" not in result.output


# -----------------------------------------------------------------------------
# EQUS Gold: refresh-gold, resumen-equ, close-month, equs-report
# -----------------------------------------------------------------------------
def _gold_report(source: str = "live") -> dict[str, Any]:
    """Return a small report of September 2026, built as the real one is."""
    from datetime import UTC, datetime  # noqa: PLC0415
    from decimal import Decimal  # noqa: PLC0415

    from energy.domain.equs_gold import (  # noqa: PLC0415
        DateRange,
        Driver,
        DriverAmount,
        QualityCount,
        ReportData,
        VersionMonth,
        nest_report,
    )

    month = date(2026, 9, 1)
    driver = Driver(
        version=1, version_name="STD FY26", cuenta_stats="OXPROD_KG", descripcion="Oxido",
        proceso="OREC", unidad="KG", fuente="PRODUCCION", estandar_por_bat=Decimal("6.08"),
        multiplicador=Decimal(1), peso=Decimal("0.074"),
        factor=Decimal(1) / Decimal("6.08") * Decimal("0.074"), suma_eq=True, fila=2,
        planta="OREC", area_planta="OREC", orden=1, peso_proceso=Decimal("0.346"),
    )  # fmt: skip
    quantity = Decimal(1162129)
    return nest_report(
        ReportData(
            period=DateRange.of_month(month),
            source="closed" if source == "closed" else "live",
            generated_at=datetime(2026, 10, 7, tzinfo=UTC),
            versions=(VersionMonth(month, 1, "STD FY26"),),
            drivers=(driver,),
            amounts=(DriverAmount(month, 1, "OXPROD_KG", quantity, quantity * driver.factor),),
            poly=(),
            quality=(QualityCount("dq_sin_regla", "no rule", bloqueante=True, filas=0),),
        )
    )


@pytest.fixture
def gold(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the Gold flows, recording how they are called."""
    from decimal import Decimal  # noqa: PLC0415

    from energy.domain.equs_gold import (  # noqa: PLC0415
        CloseSummary,
        FrozenMonth,
        QualityCount,
        RefreshStep,
        column_h,
    )
    from energy.services.equs_gold import RefreshOutcome  # noqa: PLC0415

    recorded: dict[str, Any] = {}

    def fail_if_asked() -> None:
        if "raise" in recorded:
            raise recorded["raise"]

    def fake_refresh_gold(**options: Any) -> RefreshOutcome:
        recorded["refresh"] = options
        fail_if_asked()
        return RefreshOutcome((RefreshStep("refresh silver.td_form", 0.25, "concurrently"),))

    def fake_resumen_equ(month: date, *, source: str = "live") -> tuple[Any, Any]:
        recorded["resumen"] = (month, source)
        fail_if_asked()
        report = _gold_report(source)
        return report, column_h(report)

    def fake_close_month(month: date, **options: Any) -> CloseSummary:
        recorded["close"] = (month, options)
        options["on_quality"]((QualityCount("dq_sin_regla", "no rule", bloqueante=True, filas=0),))
        fail_if_asked()
        return CloseSummary(
            frozen=FrozenMonth(month, 1, 13, reclosed=False, replaced=0, accepted_warnings=False),
            quality=(),
            processes=(("OREC", Decimal(71040)),),
            equs_batt=Decimal(71040),
            equs_poly=Decimal(503033),
        )

    def fake_report_for(start: str, end: str, source: str, by_month: bool, *, dsn: Any) -> Any:
        recorded["report"] = (start, end, source, by_month, dsn)
        fail_if_asked()
        return _gold_report(source)

    monkeypatch.setattr(cli, "refresh_gold", fake_refresh_gold)
    monkeypatch.setattr(cli, "resumen_equ", fake_resumen_equ)
    monkeypatch.setattr(cli, "close_month", fake_close_month)
    monkeypatch.setattr(cli, "latest_production_month", lambda: date(2026, 9, 1))
    monkeypatch.setattr(cli, "report_for", fake_report_for)
    return recorded


def test_help_lists_the_gold_commands() -> None:
    """Refresh, read and close are discoverable beside the loads."""
    result = runner.invoke(cli.build_app(), ["equs", "--help"])

    assert result.exit_code == 0
    for command in ("refresh-gold", "resumen-equ", "close-month"):
        assert command in result.output


def test_resumen_equ_prints_column_h(gold: dict[str, Any]) -> None:
    """A table by default: the driver, then the totals."""
    result = runner.invoke(cli.build_app(), ["equs", "resumen-equ", "--month", "2026-09"])

    assert result.exit_code == 0, result.output
    assert gold["resumen"] == (date(2026, 9, 1), "live")
    assert "Column H of RESUMEN EQU, 2026-09 (live)" in result.output
    assert "OXPROD_KG" in result.output
    assert "1,162,129" in result.output
    assert "0.0121710526" in result.output
    assert "14,144.33" in result.output
    assert "EQUS BATT" in result.output


def test_resumen_equ_writes_json_and_csv(gold: dict[str, Any]) -> None:
    """The same rows, for a machine."""
    as_json = runner.invoke(
        cli.build_app(),
        ["equs", "resumen-equ", "--month", "2026-09", "--format", "json", "--source", "closed"],
    )
    assert as_json.exit_code == 0, as_json.output
    payload = json.loads(as_json.output)
    assert payload["mes"] == "2026-09"
    assert payload["fuente"] == "closed"
    assert payload["filas"][0]["nombre"] == "OXPROD_KG"
    assert gold["resumen"][1] == "closed"

    as_csv = runner.invoke(
        cli.build_app(), ["equs", "resumen-equ", "--month", "2026-09", "--format", "CSV"]
    )
    assert as_csv.exit_code == 0, as_csv.output
    lines = as_csv.output.splitlines()
    assert lines[0] == "tipo,fila,nombre,proceso,planta,categoria,cantidad,factor,equs"
    assert lines[1].startswith("cuenta,2,OXPROD_KG,OREC,OREC,,1162129,0.0121710526,")


@pytest.mark.usefixtures("gold")
@pytest.mark.parametrize(
    "arguments",
    [
        ["equs", "resumen-equ"],
        ["equs", "resumen-equ", "--month", "2026-13"],
        ["equs", "resumen-equ", "--month", "2026-09", "--format", "xml"],
        ["equs", "resumen-equ", "--month", "2026-09", "--source", "frozen"],
        ["equs", "close-month"],
        ["equs", "refresh-gold", "--month", "September"],
    ],
)
def test_invalid_arguments_exit_1(arguments: list[str]) -> None:
    """Exit 1, with the reason, and nothing done."""
    result = runner.invoke(cli.build_app(), arguments)

    assert result.exit_code == 1
    assert "error:" in result.output


@pytest.mark.parametrize(("error", "code"), [("database", 2), ("lock", 3), ("refused", 1)])
def test_the_exit_code_says_what_went_wrong(gold: dict[str, Any], error: str, code: int) -> None:
    """2 for the database, 3 for the lock, 1 for a refused request."""
    gold["raise"] = {
        "database": RepositoryError("could not reach the database"),
        "lock": LockTimeoutError("another refresh is running"),
        "refused": CloseRefusedError("the month is already closed"),
    }[error]

    for arguments in (
        ["equs", "refresh-gold"],
        ["equs", "close-month", "2026-09"],
        ["equs", "resumen-equ", "--month", "2026-09"],
    ):
        result = runner.invoke(cli.build_app(), arguments)
        assert result.exit_code == code, (arguments, result.output)
    report = runner.invoke(cli.build_report_app(), ["--start", "2026-09-01", "--end", "2026-09-30"])
    assert report.exit_code == code


def test_close_month_prints_the_checks_and_the_close(gold: dict[str, Any]) -> None:
    """The options reach the close; the counts print before the result."""
    result = runner.invoke(
        cli.build_app(),
        [
            "equs", "close-month", "2026-09", "--accept-warnings", "--reclose",
            "--closed-by", "planner", "--lock-timeout", "5",
        ],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    month, options = gold["close"]
    assert month == date(2026, 9, 1)
    assert options["accept_warnings"] is True
    assert options["reclose"] is True
    assert options["closed_by"] == "planner"
    assert options["lock_timeout"] == 5
    assert result.output.index("Data-quality checks") < result.output.index("Closed 2026-09")
    assert "EQUS POLY" in result.output


def test_refresh_gold_prints_its_steps_then_column_h(gold: dict[str, Any]) -> None:
    """Each step with its time, then the latest month's column H."""
    result = runner.invoke(cli.build_app(), ["equs", "refresh-gold", "--no-concurrently"])

    assert result.exit_code == 0, result.output
    assert gold["refresh"] == {"with_silver": False, "concurrently": False, "lock_timeout": None}
    assert "refresh silver.td_form" in result.output
    assert "0.25 s" in result.output
    assert gold["resumen"] == (date(2026, 9, 1), "live")
    assert "Column H of RESUMEN EQU, 2026-09" in result.output


def test_refresh_gold_writes_json(gold: dict[str, Any]) -> None:
    """The steps and the month's rows, for a machine."""
    result = runner.invoke(
        cli.build_app(),
        ["equs", "refresh-gold", "--with-silver", "--month", "2026-08", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["pasos"][0]["paso"] == "refresh silver.td_form"
    assert payload["pasos"][-1]["paso"] == "compute column H"
    assert payload["resumen"]["filas"][0]["nombre"] == "OXPROD_KG"
    assert gold["refresh"]["with_silver"] is True
    assert gold["resumen"][0] == date(2026, 8, 1)


def test_equs_report_writes_json_to_stdout_or_a_file(gold: dict[str, Any], tmp_path: Path) -> None:
    """The dictionary as JSON; --output writes it to a file instead."""
    app = cli.build_report_app()
    printed = runner.invoke(app, ["--start", "2026-09-01", "--end", "2026-09-30", "--by-month"])
    assert printed.exit_code == 0, printed.output
    assert json.loads(printed.output)["equs_batt"] > 0
    assert gold["report"] == ("2026-09-01", "2026-09-30", "live", True, None)

    target = tmp_path / "september.json"
    written = runner.invoke(
        app,
        ["--start", "2026-09-01", "--end", "2026-09-30", "--source", "closed",
         "--output", str(target), "--dsn", "postgresql://u:p@h/db"],
    )  # fmt: skip
    assert written.exit_code == 0, written.output
    assert json.loads(target.read_text(encoding="utf-8"))["fuente"] == "closed"
    assert gold["report"][2:] == ("closed", False, "postgresql://u:p@h/db")


@pytest.mark.usefixtures("gold")
def test_equs_report_needs_both_dates() -> None:
    """Exit 1 without --end."""
    result = runner.invoke(cli.build_report_app(), ["--start", "2026-09-01"])

    assert result.exit_code == 1
    assert "--start and --end are both required" in result.output


@pytest.mark.parametrize(
    ("entry", "argv", "code"),
    [
        ("main", ["energy", "equs", "resumen-equ", "--no-such-option"], 1),
        ("report_main", ["equs-report", "--start", "2026-09-01", "--bogus"], 1),
        ("main", ["energy", "equs", "--help"], 0),
    ],
)
def test_a_malformed_command_line_exits_1_not_2(
    monkeypatch: pytest.MonkeyPatch, entry: str, argv: list[str], code: int
) -> None:
    """Exit 2 belongs to database errors, so usage errors give 1."""
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as finished:
        getattr(cli, entry)()
    assert finished.value.code == code


# -----------------------------------------------------------------------------
# EQUS Gold: daily
# -----------------------------------------------------------------------------
def _series(*, equal: bool = True, frequency: str = "day") -> Any:
    """Return a series of two days of September 2026, built as the real one is."""
    from decimal import Decimal  # noqa: PLC0415

    from energy.domain.equs_gold import DateRange, VersionMonth  # noqa: PLC0415
    from energy.domain.equs_series import (  # noqa: PLC0415
        BatteryEquivalents,
        ReconciledAmount,
        SeriesRow,
    )

    month = date(2026, 9, 1)
    factor = Decimal("0.354")
    # A monthly series has one row per month, dated on its first day.
    days = ((month, 150),) if frequency == "month" else ((month, 100), (date(2026, 9, 2), 50))
    rows = tuple(
        SeriesRow(start, 1, "ENSAMBLE", "Ensamble", "COSPRODU_SLI", Decimal(quantity), factor,
                  Decimal(quantity) * factor)
        for start, quantity in days
    )  # fmt: skip
    reconciled = ReconciledAmount(
        month, 1, "COSPRODU_SLI", Decimal(150), Decimal(150 if equal else 149),
        Decimal("53.1"), Decimal("53.1"), equal=equal,
    )  # fmt: skip
    return BatteryEquivalents(
        period=DateRange(month, date(2026, 9, 30)),
        frequency="month" if frequency == "month" else "day",
        procesos=("OREC", "ENSAMBLE"),
        filtered=False,
        versions=(VersionMonth(month, 1, "STD FY26"),),
        rows=rows,
        reconciliation=(reconciled,) if frequency == "day" else (),
    )


@pytest.fixture
def daily(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the series flow, recording how it is called."""
    recorded: dict[str, Any] = {"equal": True}

    def fake_battery_equivalents(period: Any, **options: Any) -> Any:
        recorded["call"] = (period, options)
        if "raise" in recorded:
            raise recorded["raise"]
        return _series(equal=recorded["equal"], frequency=options["frequency"])

    monkeypatch.setattr(cli, "battery_equivalents", fake_battery_equivalents)
    return recorded


def test_help_lists_the_daily_command() -> None:
    """Beside the other Gold commands."""
    result = runner.invoke(cli.build_app(), ["equs", "--help"])

    assert result.exit_code == 0
    assert "daily" in result.output


def test_daily_prints_a_line_per_day_and_the_reconciliation(daily: dict[str, Any]) -> None:
    """A month by default by day, every battery process, a table."""
    result = runner.invoke(cli.build_app(), ["equs", "daily", "--month", "2026-09"])

    assert result.exit_code == 0, result.output
    period, options = daily["call"]
    assert (period.start, period.end) == (date(2026, 9, 1), date(2026, 9, 30))
    assert options == {"frequency": "day", "procesos": (), "areas": ()}
    assert "Equivalent batteries by day, 2026-09-01 to 2026-09-30 (live)" in result.output
    assert "2026-09-01" in result.output
    assert "35.40" in result.output
    assert "The days add up to gold.v_resumen_equ exactly: 2026-09" in result.output


def test_daily_passes_its_options_and_writes_json_and_csv(daily: dict[str, Any]) -> None:
    """A range, by month, filtered; then the same rows for a machine."""
    as_json = runner.invoke(
        cli.build_app(),
        ["equs", "daily", "--start", "2026-08-15", "--end", "2026-09-30", "--frequency", "MONTH",
         "--process", "ENSAMBLE", "--process", "OREC", "--area", "F&A", "--format", "json"],
    )  # fmt: skip
    assert as_json.exit_code == 0, as_json.output
    period, options = daily["call"]
    assert (period.start, period.end) == (date(2026, 8, 15), date(2026, 9, 30))
    assert options == {"frequency": "month", "procesos": ["ENSAMBLE", "OREC"], "areas": ["F&A"]}
    payload = json.loads(as_json.output)
    assert payload["frecuencia"] == "month"
    assert payload["filas"][0]["cuenta_stats"] == "COSPRODU_SLI"

    as_csv = runner.invoke(
        cli.build_app(), ["equs", "daily", "--month", "2026-09", "--format", "csv"]
    )
    assert as_csv.exit_code == 0, as_csv.output
    lines = as_csv.output.splitlines()
    assert lines[0] == "periodo,version,proceso,area_planta,cuenta_stats,cantidad,factor,equs"
    assert lines[1] == "2026-09-01,1,ENSAMBLE,Ensamble,COSPRODU_SLI,100,0.354,35.4"


@pytest.mark.usefixtures("daily")
@pytest.mark.parametrize(
    "arguments",
    [
        ["equs", "daily"],
        ["equs", "daily", "--start", "2026-09-01"],
        ["equs", "daily", "--month", "2026-09", "--start", "2026-09-01", "--end", "2026-09-30"],
        ["equs", "daily", "--month", "2026-9"],
        ["equs", "daily", "--start", "2026-09-31", "--end", "2026-10-01"],
        ["equs", "daily", "--start", "2026-09-30", "--end", "2026-09-01"],
        ["equs", "daily", "--month", "2026-09", "--frequency", "week"],
        ["equs", "daily", "--month", "2026-09", "--format", "xml"],
    ],
)
def test_daily_refuses_invalid_arguments(arguments: list[str]) -> None:
    """Exit 1, with the reason."""
    result = runner.invoke(cli.build_app(), arguments)

    assert result.exit_code == 1
    assert "error:" in result.output


@pytest.mark.parametrize(("error", "code"), [("database", 2), ("refused", 1)])
def test_daily_exit_code_says_what_went_wrong(daily: dict[str, Any], error: str, code: int) -> None:
    """2 for the database, 1 for a request that cannot be answered."""
    from energy.domain.exceptions import InvalidPeriodError  # noqa: PLC0415

    daily["raise"] = {
        "database": RepositoryError("could not reach the database"),
        "refused": InvalidPeriodError("not a battery process"),
    }[error]
    result = runner.invoke(cli.build_app(), ["equs", "daily", "--month", "2026-09"])

    assert result.exit_code == code, result.output


def test_daily_fails_when_the_days_do_not_add_up(daily: dict[str, Any]) -> None:
    """The series is printed, the difference listed, and the exit is 1."""
    daily["equal"] = False
    result = runner.invoke(cli.build_app(), ["equs", "daily", "--month", "2026-09"])

    assert result.exit_code == 1
    assert "1 of 1 driver-months of 2026-09 do not add up" in result.output
    assert "error: the days do not add up to gold.v_resumen_equ" in result.output


# -----------------------------------------------------------------------------
# Energy-EQ cross: cruce
# -----------------------------------------------------------------------------
def _cross(*, mismatches: int = 0) -> Any:
    """Return a cross of F&A over two days, built as the real one is."""
    from decimal import Decimal  # noqa: PLC0415

    from energy.domain.energy_cross import (  # noqa: PLC0415
        AreaCross,
        AreaEnergy,
        CrossRow,
        EnergyCrossSeries,
        EnergySpan,
    )
    from energy.domain.equs_gold import DateRange  # noqa: PLC0415

    period = DateRange(date(2026, 9, 1), date(2026, 9, 2))
    spans = tuple(
        EnergySpan(day, day, Decimal(20_900), Decimal(1), 1)
        for day in (date(2026, 9, 1), date(2026, 9, 2))
    )
    total = EnergySpan(period.start, period.end, Decimal(41_800), Decimal(1), 2)
    energy = AreaEnergy("F&A", "SCADA-Base de datos F&A.xlsx", "day", period,
                        ("RECTIFICADOR 1",), spans, (total,), total)  # fmt: skip

    def row(span: EnergySpan) -> CrossRow:
        eq = Decimal(1000) * span.days
        return CrossRow("F&A", span.start, span.end, span.kwh, span.coverage, span.measured_days,
                        eq, {"FORMACION": eq / 2, "DECORADO": eq / 2})  # fmt: skip

    area = AreaCross(energy, ("FORMACION", "DECORADO"), tuple(row(s) for s in spans), row(total),
                     eq_mismatches=mismatches)  # fmt: skip
    return EnergyCrossSeries(period, "day", (area,))


@pytest.fixture
def cruce(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the cross flow, recording how it is called."""
    recorded: dict[str, Any] = {"mismatches": 0}

    def fake_energy_cross(period: Any, **options: Any) -> Any:
        recorded["call"] = (period, options)
        if "raise" in recorded:
            raise recorded["raise"]
        return _cross(mismatches=recorded["mismatches"])

    monkeypatch.setattr(cli, "energy_cross", fake_energy_cross)
    return recorded


def test_help_lists_the_cruce_command() -> None:
    result = runner.invoke(cli.build_app(), ["equs", "--help"])

    assert result.exit_code == 0
    assert "cruce" in result.output


def test_cruce_prints_a_table_per_area(cruce: dict[str, Any]) -> None:
    """By day by default, every area, the exports of data/."""
    from pathlib import Path  # noqa: PLC0415

    result = runner.invoke(
        cli.build_app(), ["equs", "cruce", "--start", "2026-09-01", "--end", "2026-09-02"]
    )

    assert result.exit_code == 0, result.output
    period, options = cruce["call"]
    assert (period.start, period.end) == (date(2026, 9, 1), date(2026, 9, 2))
    assert options == {"frequency": "day", "areas": (), "data_dir": Path("data")}
    assert "kWh against equivalent batteries by day, 2026-09-01 to 2026-09-02" in result.output
    assert "against FORMACION + DECORADO" in result.output
    assert "20.90" in result.output


def test_cruce_passes_its_options_and_writes_json_and_csv(
    cruce: dict[str, Any], tmp_path: Path
) -> None:
    as_json = runner.invoke(
        cli.build_app(),
        ["equs", "cruce", "--start", "2026-09-01", "--end", "2026-09-02", "--frequency", "Month",
         "--area", "F&A", "--data-dir", str(tmp_path), "--format", "json"],
    )  # fmt: skip
    assert as_json.exit_code == 0, as_json.output
    _, options = cruce["call"]
    assert options == {"frequency": "month", "areas": ["F&A"], "data_dir": tmp_path}
    payload = json.loads(as_json.output)
    assert payload["areas"][0]["total"]["kwh_por_eq"] == 20.9

    as_csv = runner.invoke(
        cli.build_app(),
        ["equs", "cruce", "--start", "2026-09-01", "--end", "2026-09-02", "--format", "csv"],
    )
    assert as_csv.exit_code == 0, as_csv.output
    lines = as_csv.output.splitlines()
    assert lines[0] == "area,periodo,desde,hasta,dias,dias_medidos,cobertura,kwh,eq,kwh_por_eq"
    assert lines[1] == "F&A,2026-09-01,2026-09-01,2026-09-01,1,1,1,20900,1000,20.9"


@pytest.mark.usefixtures("cruce")
@pytest.mark.parametrize(
    "arguments",
    [
        ["equs", "cruce", "--start", "2026-09-01"],
        ["equs", "cruce", "--start", "2026-09-31", "--end", "2026-10-01"],
        ["equs", "cruce", "--start", "2026-09-30", "--end", "2026-09-01"],
        ["equs", "cruce", "--start", "2026-09-01", "--end", "2026-09-02", "--frequency", "week"],
        ["equs", "cruce", "--start", "2026-09-01", "--end", "2026-09-02", "--format", "xml"],
    ],
)
def test_cruce_refuses_invalid_arguments(arguments: list[str]) -> None:
    """Exit 1, with the reason."""
    result = runner.invoke(cli.build_app(), arguments)

    assert result.exit_code == 1
    assert "error:" in result.output


@pytest.mark.parametrize(("error", "code"), [("database", 2), ("refused", 1), ("export", 1)])
def test_cruce_exit_code_says_what_went_wrong(cruce: dict[str, Any], error: str, code: int) -> None:
    """2 for the database, 1 for a question without an answer or an unreadable export."""
    from energy.domain.exceptions import (  # noqa: PLC0415
        InvalidPeriodError,
        SourceUnavailableError,
    )

    cruce["raise"] = {
        "database": RepositoryError("could not reach the database"),
        "refused": InvalidPeriodError("no energy source for this area"),
        "export": SourceUnavailableError("SCADA export cannot be read"),
    }[error]
    result = runner.invoke(
        cli.build_app(), ["equs", "cruce", "--start", "2026-09-01", "--end", "2026-09-02"]
    )

    assert result.exit_code == code, result.output


def test_cruce_fails_when_the_daily_eq_does_not_add_up(cruce: dict[str, Any]) -> None:
    cruce["mismatches"] = 1
    result = runner.invoke(
        cli.build_app(), ["equs", "cruce", "--start", "2026-09-01", "--end", "2026-09-02"]
    )

    assert result.exit_code == 1
    assert "error: the daily EQ does not add up to gold.v_resumen_equ" in result.output
