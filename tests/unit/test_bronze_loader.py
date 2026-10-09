"""Tests for the service that lands a workbook in Bronze.

Layer: tests (unit)

The service is exercised against in-memory stand-ins for its ports, so the
ordering it is responsible for - refuse before writing, archive before
cataloguing, record the run before landing, leave a failed run behind - is
checked without a database or a workbook file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from energy.adapters.local_storage import LocalObjectStorage
from energy.domain.exceptions import ConfigurationError, PipelineError, UnknownSourceError
from energy.domain.landing import SheetSpec, sha256_hex
from energy.ports.bronze_landing import (
    BronzeLandingRepository,
    LandingTarget,
    NewRawFile,
    NewRun,
    RawFile,
    SheetBatch,
)
from energy.ports.workbook_reader import OpenWorkbook, SheetRange, WorkbookReader
from energy.services.bronze_loader import BronzeLoader, LoadRequest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from pathlib import Path

pytestmark = pytest.mark.unit

BUCKET = "bronze-raw"
DATA = b"the bytes of a workbook"
FIXED_NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)

INFO = [
    ["Nombre", "Valor"],
    ["ID del Usuario", "ptorrm1"],
    ["Fecha de consulta", "01/10/2026 8:13:24"],
    ["Nombre Exploración", "Explorar Transacciones por Art"],
    ["Programa Exploración", "icbr011.p"],
]
CONDITIONS = [
    ["Campo", "Operador", "Valor", "Monto Hasta"],
    ["Fecha Efectiva", "rango", "2025-10-01", "2025-12-31"],
]
DATOS = [
    ["Número de transacción", "Fecha Efectiva", "Campo Equs", "Cantidad"],
    [17240425.0, datetime(2025, 12, 10), "N/A", 70000.0],  # noqa: DTZ001 - Excel has no time zones
    ["", "", "", ""],
    [17240418.0, date(2025, 10, 3), "N/A", 35000.0],
]
# What the rows above cover, which is not what CONDITIONS claims.
FOUND = (date(2025, 10, 3), date(2025, 12, 10))


class FakeWorkbook:
    """A workbook held in memory; ``fail_in`` names a sheet that breaks mid-read."""

    def __init__(
        self,
        sheets: dict[str, list[list[object]]],
        fail_in: str | None = None,
        fail_after: int = 0,
    ) -> None:
        self._sheets = sheets
        self._fail_in = fail_in
        self._fail_after = fail_after

    @property
    def sheet_names(self) -> tuple[str, ...]:
        return tuple(self._sheets)

    def sheet(self, name: str) -> SheetRange:
        return SheetRange(first_row=1, first_column=0, rows=self._rows(name))

    def _rows(self, name: str) -> Iterator[list[object]]:
        for index, row in enumerate(self._sheets[name]):
            if name == self._fail_in and index >= self._fail_after:
                raise OSError("the workbook broke while it was being read")
            yield row


class FakeReader:
    """Returns one prepared workbook whatever bytes it is given."""

    def __init__(self, workbook: FakeWorkbook) -> None:
        self.workbook = workbook

    def open_workbook(self, data: bytes) -> FakeWorkbook:
        del data
        return self.workbook


@dataclass
class StoredRun:
    run: NewRun
    state: str = "running"
    error: str = ""
    row_count: int = 0
    # What ``land`` was told the rows cover; ``None`` when it was told nothing.
    period: tuple[date | None, date | None] | None = None


@dataclass
class InMemoryRepository:
    """Keeps the ledgers and the landed rows in dictionaries.

    ``land`` is all-or-nothing, like the real one: rows are collected first and
    kept only if every sheet was read to the end.
    """

    files: dict[str, RawFile] = field(default_factory=dict)
    runs: dict[UUID, StoredRun] = field(default_factory=dict)
    rows: list[tuple[UUID, str, str | None, int, str, str, str]] = field(default_factory=list)

    def find_file(self, content_hash: str) -> RawFile | None:
        return self.files.get(content_hash)

    def register_file(self, file: NewRawFile) -> RawFile:
        return self.files.setdefault(
            file.content_hash,
            RawFile(uuid4(), file.content_hash, file.bucket, file.object_key),
        )

    def landed_sheets(
        self, raw_file_id: UUID, source_key: str
    ) -> frozenset[tuple[str, str | None]]:
        del raw_file_id, source_key
        return frozenset(
            (sheet, dataset_key)
            for run_id, sheet, dataset_key, *_ in self.rows
            if self.runs[run_id].state == "completed"
        )

    def open_run(self, run: NewRun) -> UUID:
        run_id = uuid4()
        self.runs[run_id] = StoredRun(run)
        return run_id

    def land(
        self,
        target: LandingTarget,
        sheets: Iterable[SheetBatch],
        period: Callable[[], tuple[date | None, date | None]] | None = None,
    ) -> dict[str, int]:
        collected = []
        counts: dict[str, int] = {}
        for sheet in sheets:
            for row in sheet.rows:
                collected.append(
                    (
                        target.run_id,
                        sheet.sheet_name,
                        sheet.dataset_key,
                        row.source_row,
                        row.content_hash,
                        row.payload_json,
                        sheet.source_uri,
                    )
                )
                counts[sheet.sheet_name] = counts.get(sheet.sheet_name, 0) + 1
        self.rows.extend(collected)
        stored = self.runs[target.run_id]
        stored.state, stored.row_count = "completed", len(collected)
        if period is not None:
            stored.period = period()
        return counts

    def fail_run(self, run_id: UUID, error: str) -> None:
        stored = self.runs[run_id]
        stored.state, stored.error = "failed", error


@dataclass
class Harness:
    loader: BronzeLoader
    repository: InMemoryRepository
    storage: LocalObjectStorage
    reader: FakeReader


def build(tmp_path: Path, sheets: dict[str, list[list[object]]] | None = None) -> Harness:
    """Wire a loader to in-memory ports and a directory archive."""
    workbook = FakeWorkbook(
        sheets
        if sheets is not None
        else {"Datos": DATOS, "Condiciones de búsqueda": CONDITIONS, "Info": INFO}
    )
    reader = FakeReader(workbook)
    repository = InMemoryRepository()
    storage = LocalObjectStorage(tmp_path / "archive")
    loader = BronzeLoader(reader, storage, repository, BUCKET, clock=lambda: FIXED_NOW)
    return Harness(loader, repository, storage, reader)


def request(**overrides: object) -> LoadRequest:
    values: dict[str, object] = {
        "source_key": "qad_rct_wo",
        "filename": "Explorar transacciones.xlsx",
        "data": DATA,
    }
    values.update(overrides)
    return LoadRequest(**values)


def test_the_fakes_satisfy_the_ports(tmp_path: Path) -> None:
    """The stand-ins are structurally what the service expects."""
    harness = build(tmp_path)

    assert isinstance(harness.repository, BronzeLandingRepository)
    assert isinstance(harness.reader, WorkbookReader)
    assert isinstance(harness.reader.workbook, OpenWorkbook)


def test_load_lands_the_default_sheet_and_completes_the_run(tmp_path: Path) -> None:
    """A load archives the file, records the run and lands every non-empty row."""
    harness = build(tmp_path)

    result = harness.loader.load(request())

    assert result.rows_landed == 2
    assert not result.skipped
    assert [(s.sheet_name, s.rows, s.skipped) for s in result.sheets] == [("Datos", 2, False)]

    assert result.run_id is not None
    stored = harness.repository.runs[result.run_id]
    assert stored.state == "completed"
    assert stored.row_count == 2

    content_hash = sha256_hex(DATA)
    object_key = f"1515/qad_rct_wo/2026/10/07/{content_hash}.xlsx"
    assert result.content_hash == content_hash
    assert harness.storage.get(BUCKET, object_key) == DATA
    assert harness.repository.files[content_hash].object_key == object_key

    landed = harness.repository.rows
    assert [row[3] for row in landed] == [2, 4]
    assert {row[6] for row in landed} == {f"{BUCKET}://{object_key}#Datos"}


def test_run_records_what_the_export_says_about_itself(tmp_path: Path) -> None:
    """The run carries the Info sheet and the conditions, exactly as delivered."""
    harness = build(tmp_path)

    result = harness.loader.load(request())

    assert result.run_id is not None
    run = harness.repository.runs[result.run_id].run
    assert run.flow_name == "Explorar transacciones"
    assert run.source_system == "qad"
    assert run.plant_code == "1515"
    assert (run.qad_program, run.qad_exploration, run.qad_user) == (
        "icbr011.p",
        "Explorar Transacciones por Art",
        "ptorrm1",
    )
    assert run.queried_at is not None
    assert run.queried_at.astimezone(UTC) == datetime(2026, 10, 1, 13, 13, 24, tzinfo=UTC)
    assert run.search_conditions == [
        {
            "campo": "Fecha Efectiva",
            "operador": "rango",
            "valor": "2025-10-01",
            "monto hasta": "2025-12-31",
        }
    ]
    # Nothing is derived from the conditions: the run is opened without a period.
    assert (run.window_start, run.window_end) == (None, None)
    assert run.parameters["info"]["Programa Exploración"] == "icbr011.p"
    assert run.parameters["sheets"] == [{"name": "Datos", "dataset_key": None, "header_row": 1}]


def test_the_period_is_the_first_and_last_date_found_in_the_rows(tmp_path: Path) -> None:
    """No year is assumed and none is asked for: the rows say what they cover."""
    harness = build(tmp_path)

    result = harness.loader.load(request())

    assert result.run_id is not None
    stored = harness.repository.runs[result.run_id]
    assert stored.period == FOUND
    assert (result.window_start, result.window_end) == FOUND
    # The export claims October to December; that claim is stored, not believed.
    assert stored.run.search_conditions[0]["monto hasta"] == "2025-12-31"


def test_rows_of_any_year_give_their_own_period(tmp_path: Path) -> None:
    """An older export is labelled with its own dates, like any other."""
    older = [
        DATOS[0],
        [900001.0, date(2023, 10, 2), "SLI", 10.0],
        [900002.0, date(2024, 9, 30), "SLI", 12.0],
    ]
    harness = build(tmp_path, {"Datos": older})

    result = harness.loader.load(request(filename="Explorar transacciones FY24.xlsx"))

    assert (result.window_start, result.window_end) == (date(2023, 10, 2), date(2024, 9, 30))


def test_dates_from_the_caller_replace_the_ones_found(tmp_path: Path) -> None:
    """A stated period is recorded as given and the rows are not searched."""
    harness = build(tmp_path)

    result = harness.loader.load(
        request(window_start=date(2025, 10, 1), window_end=date(2026, 9, 30))
    )

    assert result.run_id is not None
    stored = harness.repository.runs[result.run_id]
    assert (stored.run.window_start, stored.run.window_end) == (
        date(2025, 10, 1),
        date(2026, 9, 30),
    )
    assert stored.period is None
    assert stored.run.parameters["window_end"] == "2026-09-30"
    assert (result.window_start, result.window_end) == (date(2025, 10, 1), date(2026, 9, 30))


def test_a_source_that_is_not_dated_records_no_period(tmp_path: Path) -> None:
    """Structures are snapshots: a date column in them is not a period."""
    harness = build(tmp_path, {"PASTA": DATOS})

    result = harness.loader.load(
        request(source_key="qad_product_struc", sheets=(SheetSpec("PASTA"),))
    )

    assert result.run_id is not None
    assert harness.repository.runs[result.run_id].period is None
    assert (result.window_start, result.window_end) == (None, None)


def test_a_second_load_of_the_same_file_does_nothing(tmp_path: Path) -> None:
    """What a completed run already landed is not landed again."""
    harness = build(tmp_path)
    harness.loader.load(request())

    again = harness.loader.load(request())

    assert again.skipped
    assert again.run_id is None
    assert again.rows_landed == 0
    assert (again.window_start, again.window_end) == (None, None)
    assert [(s.sheet_name, s.skipped) for s in again.sheets] == [("Datos", True)]
    assert len(harness.repository.runs) == 1
    assert len(harness.repository.rows) == 2


def test_force_lands_again_under_a_new_run(tmp_path: Path) -> None:
    """Forcing a load keeps the earlier rows and adds a second copy."""
    harness = build(tmp_path)
    first = harness.loader.load(request())

    second = harness.loader.load(request(force=True))

    assert second.run_id is not None
    assert second.run_id != first.run_id
    assert second.rows_landed == 2
    assert len(harness.repository.rows) == 4
    assert len(harness.repository.files) == 1


def test_only_the_sheets_not_yet_landed_are_landed(tmp_path: Path) -> None:
    """One workbook feeds several loads; each lands what is still missing."""
    sheets = {"PASTA": DATOS, "PLACAS": DATOS, "BAT ENS": DATOS}
    harness = build(tmp_path, sheets)
    harness.loader.load(request(source_key="qad_product_struc", sheets=(SheetSpec("PASTA"),)))

    result = harness.loader.load(
        request(
            source_key="qad_product_struc",
            sheets=(SheetSpec("PASTA"), SheetSpec("PLACAS"), SheetSpec("BAT ENS")),
        )
    )

    assert [(s.sheet_name, s.rows, s.skipped) for s in result.sheets] == [
        ("PASTA", 0, True),
        ("PLACAS", 2, False),
        ("BAT ENS", 2, False),
    ]
    assert len(harness.repository.runs) == 2
    assert len(harness.repository.files) == 1


def test_manual_sheets_carry_their_dataset_key_and_header_row(tmp_path: Path) -> None:
    """A catalogue lands under its dataset key; a headerless one is keyed by letter."""
    harness = build(tmp_path, {"INSTRUCC": [["OXPROD_KG", "", "OXIDO"]]})

    result = harness.loader.load(
        request(
            source_key="manual_sheets",
            filename="EQUS BATT.xlsx",
            sheets=(SheetSpec("INSTRUCC", "instrucc", header_row=0),),
        )
    )

    assert result.rows_landed == 1
    run_id, sheet, dataset_key, source_row, _hash, payload, _uri = harness.repository.rows[0]
    assert (sheet, dataset_key, source_row) == ("INSTRUCC", "instrucc", 1)
    assert payload == '{"A":"OXPROD_KG","C":"OXIDO"}'
    assert harness.repository.runs[run_id].run.source_system == "excel"


def test_a_failure_leaves_a_failed_run_and_no_rows(tmp_path: Path) -> None:
    """If landing breaks, the run says so and none of its rows remain."""
    harness = build(tmp_path)
    harness.reader.workbook = FakeWorkbook(
        {"Datos": DATOS, "Condiciones de búsqueda": CONDITIONS, "Info": INFO},
        fail_in="Datos",
        fail_after=2,
    )

    with pytest.raises(PipelineError, match="nothing was landed") as raised:
        harness.loader.load(request())

    assert harness.repository.rows == []
    ((run_id, stored),) = harness.repository.runs.items()
    assert stored.state == "failed"
    assert "broke while it was being read" in stored.error
    assert stored.period is None
    assert raised.value.details["run_id"] == str(run_id)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"sheets": (SheetSpec("No existe"),)}, "no such sheet"),
        ({"sheets": (SheetSpec("Datos"), SheetSpec("Datos"))}, "named twice"),
        ({"sheets": (SheetSpec("Datos", "datos"),)}, "does not take a dataset key"),
        ({"source_key": "qad_product_struc"}, "no default sheets"),
        (
            {"source_key": "manual_sheets", "sheets": (SheetSpec("Datos"),)},
            "needs a lowercase snake_case dataset key",
        ),
        (
            {"source_key": "manual_sheets", "sheets": (SheetSpec("Datos", "Not Snake"),)},
            "needs a lowercase snake_case dataset key",
        ),
        ({"sheets": (SheetSpec("Datos", header_row=-1),)}, "must not be negative"),
        (
            {"window_start": date(2026, 9, 30), "window_end": date(2025, 10, 1)},
            "ends before it starts",
        ),
    ],
)
def test_a_bad_request_is_refused_before_anything_is_written(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    """A mistake in the request leaves no file, no run and no archive behind."""
    harness = build(tmp_path)

    with pytest.raises(ConfigurationError, match=message):
        harness.loader.load(request(**overrides))

    assert harness.repository.files == {}
    assert harness.repository.runs == {}
    assert harness.repository.rows == []
    assert list(harness.storage.list_objects(BUCKET)) == []


@pytest.mark.parametrize("sheet", ["REPORTE QAD", "Unit_Ship", "unit_ship"])
def test_sheets_with_excel_calculated_columns_are_never_landed(tmp_path: Path, sheet: str) -> None:
    """REPORTE QAD and Unit_Ship hold the results of the calculation being replaced."""
    harness = build(tmp_path, {sheet: DATOS})

    with pytest.raises(ConfigurationError, match="must not be landed"):
        harness.loader.load(
            request(source_key="manual_sheets", sheets=(SheetSpec(sheet, "reporte"),))
        )

    assert harness.repository.runs == {}


def test_unknown_source_key_is_refused(tmp_path: Path) -> None:
    """A source key the ledger would reject is refused up front."""
    harness = build(tmp_path)

    with pytest.raises(UnknownSourceError):
        harness.loader.load(request(source_key="sap_export"))


def test_a_file_missing_from_the_archive_is_archived_again(tmp_path: Path) -> None:
    """A catalogued file whose bytes were lost is restored on the next load."""
    harness = build(tmp_path)
    harness.loader.load(request())
    object_key = harness.repository.files[sha256_hex(DATA)].object_key
    emptied = LocalObjectStorage(tmp_path / "emptied")
    loader = BronzeLoader(harness.reader, emptied, harness.repository, BUCKET)

    loader.load(request(force=True))

    assert emptied.get(BUCKET, object_key) == DATA


def test_a_nonsense_period_stated_by_the_export_changes_nothing(tmp_path: Path) -> None:
    """The conditions are stored as delivered and never read for the period."""
    conditions = [CONDITIONS[0], ["Fecha Efectiva", "rango", "2026-09-30", "2025-10-01"]]
    harness = build(tmp_path, {"Datos": DATOS, "Condiciones de búsqueda": conditions})

    result = harness.loader.load(request())

    assert result.rows_landed == 2
    assert result.run_id is not None
    stored = harness.repository.runs[result.run_id]
    assert stored.period == FOUND
    assert stored.run.search_conditions[0]["valor"] == "2026-09-30"


def test_one_date_from_the_caller_is_recorded_alone(tmp_path: Path) -> None:
    """Stating either bound turns the search off: given and found dates never mix."""
    harness = build(tmp_path)

    result = harness.loader.load(request(window_start=date(2026, 1, 1)))

    assert result.run_id is not None
    stored = harness.repository.runs[result.run_id]
    assert (stored.run.window_start, stored.run.window_end) == (date(2026, 1, 1), None)
    assert stored.period is None
    assert (result.window_start, result.window_end) == (date(2026, 1, 1), None)


def test_rows_without_date_cells_leave_the_period_unknown(tmp_path: Path) -> None:
    """Dates typed as text are not parsed, so the run says nothing rather than guess."""
    textual = [DATOS[0], [900001.0, "2025-12-10", "N/A", 1.0]]
    harness = build(tmp_path, {"Datos": textual})

    result = harness.loader.load(request())

    assert result.run_id is not None
    assert harness.repository.runs[result.run_id].period == (None, None)
    assert (result.window_start, result.window_end) == (None, None)
