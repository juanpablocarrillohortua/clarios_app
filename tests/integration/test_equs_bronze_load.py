"""Land the real EQUS workbooks in a real database.

Layer: tests (integration)

These tests load the workbooks in ``data/raw_excels`` into a database migrated
to revision 0002 and check the result against the figures the business
documented for those files. They are the acceptance test of the Bronze layer:
if a count or an invariant here moves, either the loader changed what it keeps
or the source files changed, and both need a person to look.

The workbooks are not in the repository, so the whole module is skipped when
they are absent. Every test in the class builds on one shared load, because the
load is the expensive part.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import text

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.adapters.local_storage import LocalObjectStorage
from energy.adapters.postgres_bronze_repo import PostgresBronzeLandingRepository
from energy.domain.exceptions import PipelineError
from energy.domain.landing import SheetSpec, canonical_json
from energy.pipelines.flows.equs_bronze_load import (
    ROUTES,
    LoadAllResult,
    find_workbook,
    load_all,
    load_workbook,
)
from energy.ports.workbook_reader import SheetRange
from energy.services.bronze_loader import BronzeLoader

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from sqlalchemy.engine import Engine

    from energy.domain.landing import CellValue
    from energy.ports.workbook_reader import OpenWorkbook

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "raw_excels"
BUCKET = "bronze-raw"

_MISSING = [
    route.filename
    for route in ROUTES
    if not route.optional and find_workbook(DATA_DIR, route.filename) is None
]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(bool(_MISSING), reason=f"source workbooks not available: {_MISSING}"),
]

# (landing table, sheet name, dataset key) -> rows, from EQUS_CONTEXT section 9.4.
EXPECTED_ROWS: dict[tuple[str, str, str | None], int] = {
    ("raw_rct_wo", "Datos", None): 138_773,
    ("raw_facturas", "Datos", None): 14_458,
    ("raw_product_struc", "PASTA", None): 29,
    ("raw_product_struc", "PLACAS", None): 220,
    ("raw_product_struc", "BAT ENS", None): 8_340,
    ("raw_product_struc", "BAT FOR", None): 4_566,
    ("raw_product_struc", "BAT TERM", None): 3_413,
    ("raw_product_struc", "Datos", None): 31_907,
    ("raw_manual_sheets", "Clasif_Poly", "clasif_poly"): 1_753,
    ("raw_manual_sheets", "Lineas Produccion", "lineas_produccion"): 64,
    ("raw_manual_sheets", "INSTRUCC", "instrucc"): 15,
    ("raw_manual_sheets", "ELEMENT DECO", "element_deco"): 1_751,
    ("raw_manual_sheets", "Hoja1", "comparativo_lineas"): 50,
}
TOTAL_ROWS = sum(EXPECTED_ROWS.values())

# The figures above belong to six named workbooks. Exports of other periods may
# sit beside them in the folder, and load-all would land those too, so these
# tests load by exact name only.
EXACT_ROUTES = tuple(replace(route, many=False) for route in ROUTES)

# Rows minus distinct content hashes, per sheet of the product structures. The
# repeats in PLACAS differ in their cost columns, so they are not exact copies.
EXPECTED_EXACT_DUPLICATES = {
    "PASTA": 0,
    "PLACAS": 0,
    "BAT ENS": 13,
    "BAT FOR": 11,
    "BAT TERM": 0,
    "Datos": 790,
}

LANDING_TABLES = (
    "raw_rct_wo",
    "raw_facturas",
    "raw_product_struc",
    "raw_item_master",
    "raw_manual_sheets",
)


@dataclass(frozen=True)
class Loaded:
    """One shared load of the real workbooks."""

    engine: Engine
    loader: BronzeLoader
    storage: LocalObjectStorage
    summary: LoadAllResult


class BreakingWorkbook:
    """A workbook whose chosen sheet fails after a number of rows."""

    def __init__(self, inner: OpenWorkbook, sheet: str, after: int) -> None:
        self._inner = inner
        self._sheet = sheet
        self._after = after

    @property
    def sheet_names(self) -> tuple[str, ...]:
        return self._inner.sheet_names

    def sheet(self, name: str) -> SheetRange:
        found = self._inner.sheet(name)
        if name != self._sheet:
            return found
        return SheetRange(found.first_row, found.first_column, self._break(found.rows))

    def _break(self, rows: Iterator[Sequence[CellValue]]) -> Iterator[Sequence[CellValue]]:
        for index, row in enumerate(rows):
            if index >= self._after:
                raise OSError("the disk failed while the sheet was being read")
            yield row


class BreakingReader:
    """A reader that opens real workbooks which then fail mid-sheet."""

    def __init__(self, sheet: str, after: int) -> None:
        self._inner = CalamineWorkbookReader()
        self._sheet = sheet
        self._after = after

    def open_workbook(self, data: bytes) -> BreakingWorkbook:
        return BreakingWorkbook(self._inner.open_workbook(data), self._sheet, self._after)


def _count(engine: Engine, statement: str, **parameters: object) -> int:
    """Run a counting query and return its single value."""
    with engine.connect() as connection:
        return int(connection.execute(text(statement), parameters).scalar_one())


def _landed_rows(engine: Engine) -> dict[tuple[str, str, str | None], int]:
    """Count the landed rows per table, sheet and dataset key."""
    counts: dict[tuple[str, str, str | None], int] = {}
    with engine.connect() as connection:
        for table in LANDING_TABLES:
            dataset_key = "dataset_key" if table == "raw_manual_sheets" else "NULL"
            rows = connection.execute(
                text(
                    f"SELECT sheet_name, {dataset_key} AS dataset_key, count(*) AS n "  # noqa: S608
                    f"FROM bronze.{table} GROUP BY 1, 2"
                )
            ).all()
            for row in rows:
                counts[(table, row.sheet_name, row.dataset_key)] = row.n
    return counts


@pytest.fixture(scope="class")
def loaded(bronze_engine: Engine, tmp_path_factory: pytest.TempPathFactory) -> Loaded:
    """Load every workbook once, into a database migrated to revision 0002."""
    storage = LocalObjectStorage(tmp_path_factory.mktemp("bronze_archive"))
    loader = BronzeLoader(
        reader=CalamineWorkbookReader(),
        storage=storage,
        repository=PostgresBronzeLandingRepository(bronze_engine),
        bucket=BUCKET,
    )
    summary = load_all(loader, DATA_DIR, routes=EXACT_ROUTES)
    return Loaded(bronze_engine, loader, storage, summary)


class TestLoadAll:
    """The six workbooks, landed once, checked against the documented figures."""

    def test_every_required_route_landed(self, loaded: Loaded) -> None:
        """All seven loads complete; only the undelivered item master is absent."""
        failed = [(o.route.filename, o.error) for o in loaded.summary.outcomes if o.error]
        assert not failed

        statuses = {
            (o.route.source_key, o.route.filename): o.status
            for o in loaded.summary.outcomes
            if not o.route.optional
        }
        assert set(statuses.values()) == {"landed"}
        assert len(statuses) == 7
        assert loaded.summary.ok
        assert loaded.summary.rows_landed >= TOTAL_ROWS

    def test_row_counts_match_the_documented_ones(self, loaded: Loaded) -> None:
        """Every sheet lands exactly the rows EQUS_CONTEXT section 9.4 lists.

        A row more or less means a row was dropped, merged or invented, and
        Bronze is allowed to do none of those.
        """
        landed = _landed_rows(loaded.engine)

        assert {key: landed.get(key) for key in EXPECTED_ROWS} == EXPECTED_ROWS

    def test_runs_are_completed_with_what_the_exports_say(self, loaded: Loaded) -> None:
        """Each load is a completed run carrying the export's own metadata."""
        with loaded.engine.connect() as connection:
            runs = connection.execute(
                text(
                    "SELECT source_key, source_system, state, row_count, plant_code, "
                    "window_start, window_end, qad_program, qad_user, queried_at, "
                    "search_conditions, finished_at, flow_name "
                    "FROM bronze.ingestion_runs WHERE state = 'completed' ORDER BY started_at"
                )
            ).all()

        assert len(runs) == 7
        assert sum(run.row_count for run in runs) == TOTAL_ROWS
        assert all(run.finished_at is not None and run.plant_code == "1515" for run in runs)

        transactions = next(run for run in runs if run.source_key == "qad_rct_wo")
        assert transactions.source_system == "qad"
        assert transactions.row_count == 138_773
        assert transactions.qad_program == "icbr011.p"
        assert transactions.qad_user == "ptorrm1"
        # The export states October to December 2025; the run records what its
        # rows hold, which is the whole year documented in EQUS_CONTEXT section 3.
        assert (transactions.window_start, transactions.window_end) == (
            date(2025, 10, 1),
            date(2026, 9, 30),
        )
        assert transactions.search_conditions[0] == {
            "campo": "Fecha Efectiva",
            "operador": "rango",
            "valor": "2025-10-01",
            "monto hasta": "2025-12-31",
        }
        # "01/10/2026 8:13:24" in Bogota is 13:13:24 UTC.
        assert transactions.queried_at.astimezone(UTC) == datetime(
            2026, 10, 1, 13, 13, 24, tzinfo=UTC
        )

        invoices = next(run for run in runs if run.source_key == "qad_facturas")
        # Found in the rows too, and inside the period this export rightly states.
        assert date(2025, 10, 1) <= invoices.window_start
        assert invoices.window_start < invoices.window_end <= date(2026, 9, 30)
        assert invoices.qad_program == "zzbr007.p"
        assert len(invoices.search_conditions) == 4

        catalogue = next(run for run in runs if run.flow_name == "EQUS BATT")
        assert catalogue.source_system == "excel"
        assert catalogue.qad_program is None
        assert catalogue.search_conditions == []
        # Structures and catalogues are snapshots: they cover no period.
        undated = [run for run in runs if run.source_key not in {"qad_rct_wo", "qad_facturas"}]
        assert len(undated) == 5
        assert all(run.window_start is None and run.window_end is None for run in undated)

    @pytest.mark.parametrize(
        ("source_key", "table"),
        [("qad_rct_wo", "raw_rct_wo"), ("qad_facturas", "raw_facturas")],
    )
    def test_a_movement_run_covers_the_dates_its_rows_hold(
        self, loaded: Loaded, source_key: str, table: str
    ) -> None:
        """The period on the run is the first and last Fecha Efectiva it landed.

        Checked against the landed payloads themselves, so it holds whatever
        years the workbook in the folder happens to contain.
        """
        with loaded.engine.connect() as connection:
            run = connection.execute(
                text(
                    "SELECT run_id, window_start, window_end FROM bronze.ingestion_runs "
                    "WHERE source_key = :source_key AND state = 'completed'"
                ),
                {"source_key": source_key},
            ).one()
            held = connection.execute(
                text(
                    "SELECT min(left(payload ->> 'Fecha Efectiva', 10)) AS first, "  # noqa: S608
                    "max(left(payload ->> 'Fecha Efectiva', 10)) AS last, "
                    "count(*) FILTER (WHERE payload ->> 'Fecha Efectiva' !~ :iso_date) AS other "
                    f"FROM bronze.{table} WHERE ingestion_run_id = :run_id"
                ),
                {"run_id": run.run_id, "iso_date": r"^\d{4}-\d{2}-\d{2}"},
            ).one()

        # Every dated cell is a real date cell, so none was left out of the period.
        assert held.other == 0
        assert (run.window_start.isoformat(), run.window_end.isoformat()) == (
            held.first,
            held.last,
        )

    def test_each_workbook_is_catalogued_and_archived_once(self, loaded: Loaded) -> None:
        """Six files, though seven loads: ESTRUCTURA DECORADO feeds two of them."""
        with loaded.engine.connect() as connection:
            files = connection.execute(
                text(
                    "SELECT filename, content_hash, bucket, object_key, size_bytes, sheet_names "
                    "FROM bronze.raw_files"
                )
            ).all()

        assert len(files) == 6
        for file in files:
            path = DATA_DIR / file.filename
            data = path.read_bytes()
            assert file.content_hash == hashlib.sha256(data).hexdigest()
            assert file.size_bytes == len(data)
            assert file.bucket == BUCKET
            assert file.object_key.endswith(f"{file.content_hash}.xlsx")
            # The archive holds the exact bytes the rows were parsed from.
            assert loaded.storage.get(file.bucket, file.object_key) == data

        sheets = {file.filename: file.sheet_names for file in files}
        assert len(sheets["EQUS BATT.xlsx"]) == 24
        assert "BAT TERM" in sheets["BOM BATERIAS FORMADAS.xlsx"]

    def test_na_arrives_as_text(self, loaded: Loaded) -> None:
        """Exactly 6,582 receipts carry ``N/A`` in Campo Equs.

        ``N/A`` is a value meaning "not applicable", not a missing one. A reader
        that turned it into a null would silently drop those rows' category.
        """
        assert (
            _count(
                loaded.engine,
                "SELECT count(*) FROM bronze.raw_rct_wo WHERE payload ->> 'Campo Equs' = 'N/A'",
            )
            == 6_582
        )

    def test_repeated_structure_links_are_both_kept(self, loaded: Loaded) -> None:
        """PLACAS rows 92 and 191 are the same link from two different exports.

        They differ in standard cost, so they are not exact duplicates, and
        Bronze keeps both. Row 257, a header pasted into the data, stays too.
        """
        with loaded.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT source_row, payload FROM bronze.raw_product_struc "
                    "WHERE sheet_name = 'PLACAS' AND source_row IN (92, 191, 257) "
                    "ORDER BY source_row"
                )
            ).all()

        assert [row.source_row for row in rows] == [92, 191, 257]
        first, second, pasted_header = (row.payload for row in rows)
        for link in (first, second):
            assert link["Artículo Matriz"] == "215000015"
            assert link["Artículo Componente"] == "211000006"
        assert first["COSTO_STD"] != second["COSTO_STD"]
        assert pasted_header["Artículo Matriz"] == "Artículo Matriz"

    def test_exact_duplicates_are_preserved(self, loaded: Loaded) -> None:
        """Rows minus distinct hashes, per sheet, match the documented defects."""
        with loaded.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT sheet_name, count(*) - count(DISTINCT content_hash) AS duplicates "
                    "FROM bronze.raw_product_struc GROUP BY sheet_name"
                )
            ).all()

        assert {row.sheet_name: row.duplicates for row in rows} == EXPECTED_EXACT_DUPLICATES

    def test_a_landed_row_follows_the_payload_rules(self, loaded: Loaded) -> None:
        """The first receipt, cell by cell: text as exported, hash as specified."""
        with loaded.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT r.source_row, r.source_uri, r.source_system, r.plant_code, "
                    "r.content_hash, r.payload, f.object_key "
                    "FROM bronze.raw_rct_wo r JOIN bronze.raw_files f USING (raw_file_id) "
                    "WHERE r.sheet_name = 'Datos' AND r.source_row = 2"
                )
            ).one()

        payload = row.payload
        # A number without a trailing ".0", a decimal in full, a date in ISO form.
        assert payload["Número de transacción"] == "17240425"
        assert payload["Cambiar cantidad de ubic"] == "70000"
        assert payload["Costo Total"] == "182346891.593783"
        assert payload["Fecha Efectiva"] == "2025-12-10"
        # Text that looks like a number stays text, untouched.
        assert payload["Número Artículo"] == "111000002"
        assert payload["Campo Equs"] == "N/A"
        # Empty cells are omitted rather than stored as empty strings.
        assert "Lote/Serie" not in payload
        assert all(value != "" for value in payload.values())

        assert row.content_hash == hashlib.sha256(canonical_json(payload).encode()).hexdigest()
        assert row.source_uri == f"{BUCKET}://{row.object_key}#Datos"
        assert (row.source_system, row.plant_code) == ("qad", "1515")

    def test_headers_become_keys_as_documented(self, loaded: Loaded) -> None:
        """Repeated, blank and missing headers each get the key the contract gives them."""
        with loaded.engine.connect() as connection:
            structure = connection.execute(
                text(
                    "SELECT payload FROM bronze.raw_product_struc "
                    "WHERE sheet_name = 'BAT ENS' AND source_row = 2"
                )
            ).scalar_one()
            poly = connection.execute(
                text(
                    "SELECT payload FROM bronze.raw_manual_sheets "
                    "WHERE dataset_key = 'clasif_poly' AND source_row = 2"
                )
            ).scalar_one()
            notes = connection.execute(
                text(
                    "SELECT source_row, payload FROM bronze.raw_manual_sheets "
                    "WHERE dataset_key = 'instrucc' ORDER BY source_row"
                )
            ).all()
            lines = connection.execute(
                text(
                    "SELECT payload FROM bronze.raw_manual_sheets "
                    "WHERE dataset_key = 'comparativo_lineas' AND source_row = 2"
                )
            ).scalar_one()

        # The export has two columns headed "Descripción".
        assert {"Descripción", "Descripción #2"} <= set(structure)
        # Clasif_Poly repeats three headers in a side table.
        assert {"Campo Equs", "Campo Equs #2", "FACTOR EQU #2"} <= set(poly)
        # INSTRUCC has no header row: 15 rows from row 1, keyed by column letter.
        assert [row.source_row for row in notes] == list(range(1, 16))
        assert notes[0].payload["A"] == "OXPROD_KG"
        assert set(notes[0].payload) <= {"A", "B", "C", "D"}
        # Hoja1 starts in column B and its first header has a trailing space.
        assert lines["Planta"] == "RC"

    def test_a_second_load_all_lands_nothing(self, loaded: Loaded) -> None:
        """Loading the same files again is a no-op: no rows, no runs."""
        before = _landed_rows(loaded.engine)
        runs_before = _count(loaded.engine, "SELECT count(*) FROM bronze.ingestion_runs")

        again = load_all(loaded.loader, DATA_DIR, routes=EXACT_ROUTES)

        assert again.rows_landed == 0
        assert {o.status for o in again.outcomes if not o.route.optional} == {"skipped"}
        assert again.ok
        assert _landed_rows(loaded.engine) == before
        assert _count(loaded.engine, "SELECT count(*) FROM bronze.ingestion_runs") == runs_before
        assert _count(loaded.engine, "SELECT count(*) FROM bronze.raw_files") == 6

    def test_a_forced_failure_leaves_a_failed_run_and_no_rows(self, loaded: Loaded) -> None:
        """A load that breaks mid-way is rolled back whole and recorded as failed.

        The sheet fails after 6,000 rows, so one full batch of BAT ENS and all
        of PASTA were already sent to the database when it broke. None of it may
        remain.
        """
        before = _landed_rows(loaded.engine)
        breaking = BronzeLoader(
            reader=BreakingReader("BAT ENS", after=6_000),
            storage=loaded.storage,
            repository=PostgresBronzeLandingRepository(loaded.engine),
            bucket=BUCKET,
        )
        workbook = find_workbook(DATA_DIR, "BOM BATERIAS FORMADAS.xlsx")
        assert workbook is not None

        with pytest.raises(PipelineError, match="nothing was landed") as raised:
            load_workbook(
                breaking,
                "qad_product_struc",
                workbook,
                (SheetSpec("PASTA"), SheetSpec("BAT ENS")),
                force=True,
            )

        run_id = UUID(raised.value.details["run_id"])
        with loaded.engine.connect() as connection:
            run = connection.execute(
                text(
                    "SELECT state, error, row_count, finished_at "
                    "FROM bronze.ingestion_runs WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ).one()

        assert run.state == "failed"
        assert "the disk failed" in run.error
        assert run.row_count == 0
        assert run.finished_at is not None
        assert (
            _count(
                loaded.engine,
                "SELECT count(*) FROM bronze.raw_product_struc WHERE ingestion_run_id = :run_id",
                run_id=run_id,
            )
            == 0
        )
        assert _landed_rows(loaded.engine) == before

    def test_force_lands_a_second_copy_under_a_new_run(self, loaded: Loaded) -> None:
        """Forcing a small sheet adds its rows again; Bronze keeps both copies."""
        workbook = find_workbook(DATA_DIR, "BOM BATERIAS FORMADAS.xlsx")
        assert workbook is not None

        result = load_workbook(
            loaded.loader, "qad_product_struc", workbook, (SheetSpec("PASTA"),), force=True
        )

        assert result.rows_landed == 29
        assert (
            _count(
                loaded.engine,
                "SELECT count(*) FROM bronze.raw_product_struc WHERE sheet_name = 'PASTA'",
            )
            == 58
        )
        payloads = _count(
            loaded.engine,
            "SELECT count(DISTINCT content_hash) FROM bronze.raw_product_struc "
            "WHERE sheet_name = 'PASTA'",
        )
        assert payloads == 29
