"""Tests for the command-line entry point.

Layer: tests (unit)

The commands are thin: they parse arguments, call the load functions and print.
So these tests replace the load functions and check the three things the CLI
owns - how arguments are turned into a request, what is printed, and the exit
code a shell or a scheduler will see.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from typer.testing import CliRunner

import energy.__main__ as cli
from energy.domain.exceptions import PipelineError
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
