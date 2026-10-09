"""Tests for the load-all routing of the EQUS workbooks.

Layer: tests (unit)

The loader itself is replaced by a recorder here: these tests are about which
workbook is sent where, in what order, and what happens when one is missing or
fails - not about reading a workbook.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from energy.adapters.local_storage import LocalObjectStorage
from energy.adapters.minio_storage import MinioObjectStorage
from energy.core.settings import get_settings
from energy.domain.exceptions import PipelineError, SourceUnavailableError
from energy.pipelines.flows.equs_bronze_load import (
    ROUTES,
    RouteOutcome,
    _normalised,
    build_loader,
    build_storage,
    find_workbook,
    find_workbooks,
    load_all,
    load_workbook,
)
from energy.services.bronze_loader import BronzeLoader, LoadRequest, LoadResult, SheetOutcome

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

# The names as they are on disk, which differ from the routing table in spaces.
ON_DISK = (
    "Calculo de equivalente- Explorar transacciones por art.xlsx",
    "Calculo de equivalente - Unidades facturadas.xlsx",
    "BOM BATERIAS FORMADAS.xlsx",
    "ESTRUCTURA DECORADO.xlsx",
    "EQUS BATT.xlsx",
    "Calculo de equivalente- Comparativo lineas.xlsx",
)
# Exports of other periods, delivered beside the current ones.
OTHER_PERIODS = (
    "Calculo de equivalente- Explorar transacciones por art FY24.xlsx",
    "Calculo de equivalente - Explorar transacciones por art FY25.xlsx",
    "Calculo de equivalente - Unidades facturadas 2024.xlsx",
)


class RecordingLoader:
    """Records each request and answers as if every sheet had landed one row."""

    def __init__(self, fail_for: str | None = None, already_landed: bool = False) -> None:
        self.requests: list[LoadRequest] = []
        self._fail_for = fail_for
        self._already_landed = already_landed

    def load(self, request: LoadRequest) -> LoadResult:
        self.requests.append(request)
        if self._fail_for and self._fail_for in request.filename:
            raise PipelineError("load failed and nothing was landed")
        return LoadResult(
            source_key=request.source_key,
            filename=request.filename,
            content_hash="0" * 64,
            run_id=None if self._already_landed else uuid4(),
            sheets=tuple(
                SheetOutcome(
                    sheet.name,
                    sheet.dataset_key,
                    0 if self._already_landed else 1,
                    skipped=self._already_landed,
                )
                for sheet in request.sheets
            ),
        )


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    """Return a directory holding empty files named like the real workbooks."""
    for name in ON_DISK:
        (tmp_path / name).write_bytes(b"x")
    return tmp_path


@pytest.mark.parametrize(
    ("on_disk", "asked_for"),
    [
        # A space before the hyphen, or not.
        (
            "Calculo de equivalente- Explorar transacciones por art.xlsx",
            "Calculo de equivalente - Explorar transacciones por art.xlsx",
        ),
        (
            "Calculo de equivalente - Comparativo lineas.xlsx",
            "Calculo de equivalente- Comparativo lineas.xlsx",
        ),
        # Case, underscores, accents and the workbook extension.
        ("item master.XLSX", "Item_Master.xlsx"),
        (
            "Cálculo de equivalente - Unidades facturadas.xlsm",
            "Calculo de equivalente - Unidades facturadas.xlsx",
        ),
    ],
)
def test_find_workbook_tolerates_how_the_name_was_typed(
    tmp_path: Path, on_disk: str, asked_for: str
) -> None:
    """The same export arrives with small differences in its file name."""
    (tmp_path / on_disk).write_bytes(b"x")

    found = find_workbook(tmp_path, asked_for)

    assert found is not None
    assert found.name == on_disk


def test_find_workbook_ignores_lock_files_and_other_files(tmp_path: Path) -> None:
    """Excel's lock file and a text file of the same name are not the workbook."""
    (tmp_path / "~$EQUS BATT.xlsx").write_bytes(b"x")
    (tmp_path / "EQUS BATT.txt").write_bytes(b"x")
    (tmp_path / "EQUS BATT backup.xlsx").write_bytes(b"x")

    assert find_workbook(tmp_path, "EQUS BATT.xlsx") is None
    assert find_workbook(tmp_path / "missing", "EQUS BATT.xlsx") is None


def test_routes_cover_every_sheet_the_calculation_consumes() -> None:
    """The routing table is the list of what load-all lands, in load order."""
    landed = [
        (route.source_key, route.filename, tuple(sheet.name for sheet in route.sheets))
        for route in ROUTES
    ]

    assert landed == [
        ("qad_rct_wo", "Calculo de equivalente - Explorar transacciones por art.xlsx", ()),
        ("qad_facturas", "Calculo de equivalente - Unidades facturadas.xlsx", ()),
        (
            "qad_product_struc",
            "BOM BATERIAS FORMADAS.xlsx",
            ("PASTA", "PLACAS", "BAT ENS", "BAT FOR", "BAT TERM"),
        ),
        ("qad_product_struc", "ESTRUCTURA DECORADO.xlsx", ("Datos",)),
        ("manual_sheets", "EQUS BATT.xlsx", ("Clasif_Poly", "Lineas Produccion", "INSTRUCC")),
        ("manual_sheets", "ESTRUCTURA DECORADO.xlsx", ("ELEMENT DECO",)),
        ("manual_sheets", "Calculo de equivalente- Comparativo lineas.xlsx", ("Hoja1",)),
        ("item_master", "Item_Master.xlsx", ()),
    ]
    forbidden = {"REPORTE QAD", "Unit_Ship"}
    assert not forbidden & {sheet.name for route in ROUTES for sheet in route.sheets}


def test_load_all_sends_each_workbook_to_its_source_in_order(directory: Path) -> None:
    """Movements, then structures, then catalogues; the item master is not there yet."""
    loader = RecordingLoader()
    seen: list[RouteOutcome] = []

    summary = load_all(loader, directory, on_outcome=seen.append)

    assert [(r.source_key, r.filename) for r in loader.requests] == [
        ("qad_rct_wo", ON_DISK[0]),
        ("qad_facturas", ON_DISK[1]),
        ("qad_product_struc", ON_DISK[2]),
        ("qad_product_struc", ON_DISK[3]),
        ("manual_sheets", ON_DISK[4]),
        ("manual_sheets", ON_DISK[3]),
        ("manual_sheets", ON_DISK[5]),
    ]
    assert [outcome.status for outcome in summary.outcomes] == ["landed"] * 7 + ["missing"]
    assert seen == list(summary.outcomes)
    # A missing item master is expected, so the load as a whole is fine.
    assert summary.ok
    # One row per named sheet; the two default-sheet routes name none here.
    assert summary.rows_landed == 11


def test_load_all_states_no_period_for_any_workbook(directory: Path) -> None:
    """No year is assumed: each run takes its period from the rows it lands."""
    loader = RecordingLoader()

    load_all(loader, directory)

    assert {(request.window_start, request.window_end) for request in loader.requests} == {
        (None, None)
    }


def test_every_export_of_a_movement_route_is_loaded(directory: Path) -> None:
    """Exports of other periods sit beside the current ones and are all landed."""
    for name in OTHER_PERIODS:
        (directory / name).write_bytes(b"x")
    loader = RecordingLoader()

    summary = load_all(loader, directory)

    movements = [
        (request.source_key, request.filename)
        for request in loader.requests
        if request.source_key in {"qad_rct_wo", "qad_facturas"}
    ]
    # Per route, the plain name first and then the others in name order.
    assert movements == [
        ("qad_rct_wo", ON_DISK[0]),
        ("qad_rct_wo", OTHER_PERIODS[0]),
        ("qad_rct_wo", OTHER_PERIODS[1]),
        ("qad_facturas", ON_DISK[1]),
        ("qad_facturas", OTHER_PERIODS[2]),
    ]
    assert [outcome.status for outcome in summary.outcomes] == ["landed"] * 10 + ["missing"]
    assert summary.unmatched == ()
    assert summary.ok


def test_a_snapshot_workbook_is_loaded_under_its_exact_name_only(directory: Path) -> None:
    """Structures and catalogues are one file each: a second version is not landed."""
    extras = ("EQUS BATT backup.xlsx", "BOM BATERIAS FORMADAS 2024.xlsx")
    for name in extras:
        (directory / name).write_bytes(b"x")
    loader = RecordingLoader()

    summary = load_all(loader, directory)

    assert len(loader.requests) == 7
    assert not {request.filename for request in loader.requests} & set(extras)
    assert {path.name for path in summary.unmatched} == set(extras)
    # Leaving a workbook out is reported, not an error.
    assert summary.ok


def test_workbooks_no_route_takes_are_reported(directory: Path) -> None:
    """A name that does not start like a routed one is listed, never loaded in silence."""
    stray = "FY24 Calculo de equivalente - Explorar transacciones por art.xlsx"
    (directory / stray).write_bytes(b"x")
    (directory / "~$EQUS BATT.xlsx").write_bytes(b"x")
    (directory / "notas.txt").write_bytes(b"x")

    summary = load_all(RecordingLoader(), directory)

    # Excel's lock file and files that are not workbooks are not worth a mention.
    assert [path.name for path in summary.unmatched] == [stray]


def test_find_workbooks_follows_the_kind_of_route(directory: Path) -> None:
    """A movement route takes every export; any other route takes its one workbook."""
    for name in OTHER_PERIODS:
        (directory / name).write_bytes(b"x")
    (directory / "EQUS BATT backup.xlsx").write_bytes(b"x")
    transactions, catalogue, item_master = ROUTES[0], ROUTES[4], ROUTES[-1]

    assert [path.name for path in find_workbooks(directory, transactions)] == [
        ON_DISK[0],
        OTHER_PERIODS[0],
        OTHER_PERIODS[1],
    ]
    assert [path.name for path in find_workbooks(directory, catalogue)] == ["EQUS BATT.xlsx"]
    assert find_workbooks(directory, item_master) == ()
    assert find_workbooks(directory / "missing", transactions) == ()


def test_only_the_movement_routes_take_several_files() -> None:
    """And no routed name begins another, so a workbook follows one route at most."""
    assert [route.source_key for route in ROUTES if route.many] == ["qad_rct_wo", "qad_facturas"]

    names = sorted({_normalised(route.filename) for route in ROUTES})
    clashes = [
        (short, long)
        for short in names
        for long in names
        if short != long and long.startswith(short)
    ]
    assert clashes == []


def test_load_all_applies_the_routes_it_is_given(directory: Path) -> None:
    """With exact-name routes, an export of another period is left alone."""
    (directory / OTHER_PERIODS[0]).write_bytes(b"x")
    loader = RecordingLoader()
    exact = tuple(replace(route, many=False) for route in ROUTES)

    summary = load_all(loader, directory, routes=exact)

    assert len(loader.requests) == 7
    assert [path.name for path in summary.unmatched] == [OTHER_PERIODS[0]]


def test_instrucc_is_read_without_a_header_row(directory: Path) -> None:
    """INSTRUCC is a list of notes; its first row is data."""
    loader = RecordingLoader()

    load_all(loader, directory)

    equs_batt = next(r for r in loader.requests if r.filename == "EQUS BATT.xlsx")
    assert [(s.name, s.dataset_key, s.header_row) for s in equs_batt.sheets] == [
        ("Clasif_Poly", "clasif_poly", 1),
        ("Lineas Produccion", "lineas_produccion", 1),
        ("INSTRUCC", "instrucc", 0),
    ]


def test_optional_sheets_are_landed_only_on_request(directory: Path) -> None:
    """L.PRODUCCION shares its sheet with pivot tables, so it is opt-in."""
    loader = RecordingLoader()

    load_all(loader, directory, include_optional=True)

    equs_batt = next(r for r in loader.requests if r.filename == "EQUS BATT.xlsx")
    assert (equs_batt.sheets[-1].name, equs_batt.sheets[-1].header_row) == ("L.PRODUCCION", 3)


def test_a_missing_workbook_is_reported_without_stopping_the_others(directory: Path) -> None:
    """One absent file fails the load as a whole but not the other six loads."""
    (directory / "BOM BATERIAS FORMADAS.xlsx").unlink()
    loader = RecordingLoader()

    summary = load_all(loader, directory)

    statuses = {(o.route.source_key, o.route.filename): o.status for o in summary.outcomes}
    assert statuses[("qad_product_struc", "BOM BATERIAS FORMADAS.xlsx")] == "missing"
    assert len(loader.requests) == 6
    assert not summary.ok


def test_a_failed_route_is_reported_without_stopping_the_others(directory: Path) -> None:
    """A workbook that fails to load does not take the rest down with it."""
    loader = RecordingLoader(fail_for="Unidades facturadas")

    summary = load_all(loader, directory)

    failed = [outcome for outcome in summary.outcomes if outcome.status == "failed"]
    assert [outcome.route.source_key for outcome in failed] == ["qad_facturas"]
    assert "nothing was landed" in failed[0].error
    assert len(loader.requests) == 7
    assert not summary.ok


def test_a_second_load_all_reports_everything_as_already_landed(directory: Path) -> None:
    """When every sheet was landed before, each route is skipped and nothing lands."""
    summary = load_all(RecordingLoader(already_landed=True), directory)

    assert [outcome.status for outcome in summary.outcomes] == ["skipped"] * 7 + ["missing"]
    assert summary.rows_landed == 0
    assert summary.ok


def test_load_workbook_reports_a_file_it_cannot_read(tmp_path: Path) -> None:
    """An unreadable path is a clear error, raised before the loader is involved."""
    loader = RecordingLoader()

    with pytest.raises(SourceUnavailableError):
        load_workbook(loader, "qad_rct_wo", tmp_path / "missing.xlsx")

    assert loader.requests == []


def test_build_storage_prefers_an_explicit_directory(tmp_path: Path) -> None:
    """`--archive-dir` wins over whatever backend is configured."""
    assert isinstance(build_storage(tmp_path), LocalObjectStorage)


@pytest.mark.parametrize(
    ("backend", "expected"),
    [("local", LocalObjectStorage), ("minio", MinioObjectStorage)],
)
def test_build_storage_follows_the_configured_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend: str, expected: type
) -> None:
    """Without a directory, ARCHIVE_BACKEND decides where originals are kept."""
    monkeypatch.setenv("ARCHIVE_BACKEND", backend)
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        # Building the MinIO adapter opens no connection, so this needs no server.
        assert isinstance(build_storage(), expected)
    finally:
        get_settings.cache_clear()


def test_build_loader_wires_the_adapters_without_connecting(tmp_path: Path) -> None:
    """The loader is assembled lazily: nothing is contacted until a load runs."""
    engine = create_engine("postgresql+psycopg://nobody:nothing@127.0.0.1:1/nowhere")

    loader = build_loader(engine=engine, archive_dir=tmp_path)

    assert isinstance(loader, BronzeLoader)
