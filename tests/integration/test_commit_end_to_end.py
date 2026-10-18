"""Committing a draft into Bronze, against a real database and a real MinIO.

Layer: tests (integration)

What "done" means for increment 7 of docs/frontend-plan.md, checked here:

* a validated draft lands with all the metadata of section 6: every field of
  the run, of the file's catalogue entry and of each landed row is read back
  and compared;
* a double submit creates one run: two commits of one draft at the same time
  leave exactly one run naming it;
* the row counts match: the rows read, the rows landed and the run's count,
  an exact duplicate row included.

And what the commit promises besides:

* it is one transaction: a failure after the rows landed, and a backend
  killed in the middle, leave every Bronze, Silver and Gold table exactly as
  it was, and the draft still validated with its rows;
* the command-line loader and the commit land one file the same way;
* a catalogue's difference is computed in SQL against the current version,
  and two new versions confirmed at once do not both land;
* the originals in MinIO keep their bytes and their metadata;
* the history reads every load.

The PostgreSQL adapters are held to the promises the in-memory stand-ins of
the unit tests keep (``tests/fixtures/loads.py``).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import date
from typing import TYPE_CHECKING, Any, Final

import pytest
from sqlalchemy import text
from tests.fixtures.workbooks import (
    INVOICES_INFO,
    LINES_SHEET,
    invoices_good,
    lines_catalogue,
    lines_catalogue_changed,
    lines_good,
)

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.adapters.minio_storage import MinioObjectStorage
from energy.adapters.postgres_bronze_repo import PostgresBronzeLandingRepository
from energy.adapters.postgres_draft_repo import PostgresDraftRepository
from energy.adapters.postgres_load_committer import PostgresLoadCommitter
from energy.adapters.postgres_load_ledger import PostgresLoadLedger
from energy.adapters.yaml_dataset_specs import load_dataset_specs
from energy.adapters.zip_package import ZipPackageInspector
from energy.domain.draft_commit import LoadOrigin, RowChange
from energy.domain.drafts import XLSX_MEDIA_TYPE, DraftStatus
from energy.domain.exceptions import (
    CurrentVersionChangedError,
    DraftBusyError,
    EnergyError,
    ValidationChangedError,
)
from energy.domain.landing import SheetSpec, landing_rows
from energy.ports.load_ledger import LoadFilter, LoadSort
from energy.services.bronze_loader import BronzeLoader, LoadRequest
from energy.services.draft_service import DraftService
from energy.services.load_service import CommitRequest, LoadService

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine

    from energy.core.settings import MinioSettings
    from energy.domain.dataset_spec import DatasetCatalogue
    from energy.domain.drafts import Draft
    from energy.services.load_service import CommitOutcome

pytestmark = pytest.mark.integration

BUCKET: Final = "bronze-raw-commits"
OWNER: Final = "00000000-0000-4000-8000-00000000abcd"
PLANTS: Final = ("1515",)


class Body:
    """An upload's body, handed over 64 KiB at a time."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._at = 0

    def read(self, size: int = -1, /) -> bytes:
        size = 65_536 if size < 0 else min(size, 65_536)
        chunk = self._data[self._at : self._at + size]
        self._at += len(chunk)
        return chunk


def layers_snapshot(engine: Engine) -> dict[str, tuple[int, str]]:
    """Return, per table of Bronze, Silver and Gold, its row count and a digest of every row."""
    snapshot: dict[str, tuple[int, str]] = {}
    with engine.connect() as connection:
        tables = connection.execute(
            text(
                "SELECT table_schema, table_name FROM information_schema.tables"
                " WHERE table_schema IN ('bronze', 'silver', 'gold')"
                " AND table_type = 'BASE TABLE' ORDER BY 1, 2"
            )
        ).all()
        for schema, table in tables:
            # The names come from the catalogue, not from outside.
            count, digest = connection.execute(
                text(
                    f"SELECT count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), ''))"  # noqa: S608
                    f' FROM "{schema}"."{table}" AS t'
                )
            ).one()
            snapshot[f"{schema}.{table}"] = (int(count), str(digest))
    return snapshot


def staged_rows(engine: Engine, draft_id: object) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM staging.draft_rows WHERE draft_id = :d"), {"d": draft_id}
            ).scalar_one()
        )


@pytest.fixture(scope="module")
def catalogue(repo_root: Path) -> DatasetCatalogue:
    return load_dataset_specs(repo_root / "config")


@pytest.fixture(scope="module")
def storage(minio_settings: MinioSettings) -> MinioObjectStorage:
    adapter = MinioObjectStorage(minio_settings)
    adapter.ensure_bucket(BUCKET)
    return adapter


class TestCommitInTheDatabase:
    @pytest.fixture
    def drafts(
        self,
        gold_engine: Engine,
        storage: MinioObjectStorage,
        catalogue: DatasetCatalogue,
        tmp_path: Path,
    ) -> DraftService:
        return DraftService(
            repository=PostgresDraftRepository(gold_engine),
            storage=storage,
            reader=CalamineWorkbookReader(),
            inspector=ZipPackageInspector(),
            catalogue=catalogue,
            bucket=BUCKET,
            temp_dir=tmp_path,
        )

    @pytest.fixture
    def loads(
        self,
        gold_engine: Engine,
        drafts: DraftService,
        storage: MinioObjectStorage,
        catalogue: DatasetCatalogue,
    ) -> LoadService:
        return LoadService(
            drafts=drafts,
            committer=PostgresLoadCommitter(gold_engine),
            ledger=PostgresLoadLedger(gold_engine),
            storage=storage,
            catalogue=catalogue,
        )

    @pytest.fixture
    def clean(self, gold_engine: Engine) -> Engine:
        """Empty Bronze and staging, so each test reads only its own loads."""
        with gold_engine.begin() as connection:
            for table in ("raw_facturas", "raw_manual_sheets", "ingestion_runs", "raw_files"):
                connection.execute(text(f"DELETE FROM bronze.{table}"))  # noqa: S608 - fixed names
            connection.execute(text("DELETE FROM staging.drafts"))
        return gold_engine

    @staticmethod
    def validated(
        drafts: DraftService,
        data: bytes,
        dataset: str = "lineas_produccion",
        name: str = "Líneas.xlsx",
    ) -> Draft:
        draft = drafts.open_upload(dataset, name, XLSX_MEDIA_TYPE, len(data), OWNER, "Ada Lovelace")
        uploaded = drafts.receive(draft, Body(data)).draft
        return drafts.validate(uploaded.draft_id, OWNER)

    @staticmethod
    def confirm(loads: LoadService, draft: Draft, accept: bool = False) -> CommitOutcome:
        shown = loads.confirmation(draft.draft_id, OWNER)
        return loads.commit(
            draft.draft_id,
            OWNER,
            CommitRequest(
                validation_digest=shown.validation_digest or "",
                accept_warnings=accept,
                base_run_id=shown.current_version.run_id if shown.current_version else None,
            ),
        )

    # ------------------------------------------------------------------
    # Done when: all the metadata, the counts
    # ------------------------------------------------------------------
    def test_a_movement_lands_with_all_the_metadata_of_section_6(
        self,
        loads: LoadService,
        drafts: DraftService,
        storage: MinioObjectStorage,
        catalogue: DatasetCatalogue,
        clean: Engine,
    ) -> None:
        data, expected = invoices_good(catalogue.get("qad_facturas"))
        draft = self.validated(drafts, data, "qad_facturas", "Facturas octubre.xlsx")
        before = layers_snapshot(clean)
        original_before = storage.stat(BUCKET, draft.object_key or "")

        outcome = self.confirm(loads, draft)

        run_id = outcome.load.summary.run_id
        with clean.connect() as connection:
            run = connection.execute(
                text("SELECT * FROM bronze.ingestion_runs WHERE run_id = :r"), {"r": run_id}
            ).one()
            archived = connection.execute(text("SELECT * FROM bronze.raw_files")).one()
            landed = connection.execute(
                text("SELECT * FROM bronze.raw_facturas ORDER BY source_row")
            ).all()
            committed = connection.execute(
                text("SELECT status, run_id, committed_at FROM staging.drafts WHERE draft_id = :d"),
                {"d": draft.draft_id},
            ).one()
            issues = connection.execute(
                text("SELECT count(*) FROM staging.draft_rule_counts WHERE draft_id = :d"),
                {"d": draft.draft_id},
            ).scalar_one()
        # The run.
        assert (run.flow_name, run.source_key, run.source_system) == (
            "Facturas octubre",
            "qad_facturas",
            "qad",
        )
        assert (run.plant_code, run.state, run.error) == ("1515", "completed", "")
        assert (run.window_start, run.window_end) == (date(2025, 10, 3), date(2025, 10, 31))
        assert (run.qad_program, run.qad_exploration, run.qad_user) == (
            INVOICES_INFO["Programa Exploración"],
            INVOICES_INFO["Nombre Exploración"],
            INVOICES_INFO["ID del Usuario"],
        )
        assert run.queried_at is not None
        assert len(run.search_conditions) == expected["conditions"]
        assert run.finished_at is not None
        assert run.row_count == expected["rows"]
        parameters = run.parameters
        assert {name: parameters[name] for name in ("filename", "content_hash", "plant_code")} == {
            "filename": "Facturas octubre.xlsx",
            "content_hash": draft.content_hash,
            "plant_code": "1515",
        }
        assert parameters["sheets"] == [{"name": "Datos", "dataset_key": None, "header_row": 1}]
        assert (parameters["force"], parameters["window_start"], parameters["window_end"]) == (
            False,
            None,
            None,
        )
        assert (parameters["uploaded_by"], parameters["uploaded_by_name"]) == (
            OWNER,
            "Ada Lovelace",
        )
        assert parameters["info"] == INVOICES_INFO
        assert (parameters["input_mode"], parameters["dataset_key"]) == ("upload", "qad_facturas")
        assert parameters["draft_id"] == str(draft.draft_id)
        assert parameters["validation"]["rows_read"] == expected["rows"]
        assert parameters["validation"]["accepted"] is None
        assert "difference" not in parameters
        self._check_file(archived, draft, data, expected)
        self._check_rows(landed, data, expected, run_id, archived.raw_file_id, draft)
        # The draft.
        assert (committed.status, committed.run_id) == ("committed", run_id)
        assert committed.committed_at is not None
        assert staged_rows(clean, draft.draft_id) == 0
        assert issues == 0  # a sound file
        # Bronze changed by exactly this load; Silver and Gold not at all.
        after = layers_snapshot(clean)
        changed = {table for table in before if before[table] != after[table]}
        assert changed == {"bronze.ingestion_runs", "bronze.raw_files", "bronze.raw_facturas"}
        # The original is untouched.
        original_after = storage.stat(BUCKET, draft.object_key or "")
        assert (original_after.content_hash, original_after.size_bytes) == (
            original_before.content_hash,
            original_before.size_bytes,
        )
        assert hashlib.sha256(storage.get(BUCKET, draft.object_key or "")).hexdigest() == (
            draft.content_hash
        )

    @staticmethod
    def _check_file(archived: Any, draft: Draft, data: bytes, expected: dict[str, Any]) -> None:
        """The file's catalogue entry is the original archived at upload."""
        assert (archived.content_hash, archived.bucket, archived.object_key) == (
            draft.content_hash,
            BUCKET,
            draft.object_key,
        )
        assert (archived.filename, archived.content_type, archived.size_bytes) == (
            "Facturas octubre.xlsx",
            XLSX_MEDIA_TYPE,
            len(data),
        )
        assert (archived.plant_code, archived.source_key, archived.uploaded_by) == (
            "1515",
            "qad_facturas",
            OWNER,
        )
        assert list(archived.sheet_names) == expected["sheet_names"]

    @staticmethod
    def _check_rows(
        landed: list[Any],
        data: bytes,
        expected: dict[str, Any],
        run_id: object,
        raw_file_id: object,
        draft: Draft,
    ) -> None:
        """Every row lands, the duplicate included, as the command line would land it."""
        sheet = CalamineWorkbookReader().open_workbook(data).sheet("Datos")
        wanted = list(landing_rows(sheet.rows))
        assert len(landed) == expected["rows"] == len(wanted)
        assert [(row.source_row, row.content_hash) for row in landed] == [
            (row.source_row, row.content_hash) for row in wanted
        ]
        assert [row.payload for row in landed] == [json.loads(row.payload_json) for row in wanted]
        assert landed[2].content_hash == landed[3].content_hash
        for row in landed:
            assert (row.ingestion_run_id, row.raw_file_id) == (run_id, raw_file_id)
            assert (row.sheet_name, row.plant_code, row.source_system) == ("Datos", "1515", "qad")
            assert row.source_uri == f"{BUCKET}://{draft.object_key}#Datos"

    def test_a_double_submit_creates_one_run(
        self, loads: LoadService, drafts: DraftService, clean: Engine
    ) -> None:
        draft = self.validated(drafts, lines_good())
        shown = loads.confirmation(draft.draft_id, OWNER)
        request = CommitRequest(validation_digest=shown.validation_digest or "")
        outcomes: list[object] = []
        start = threading.Barrier(2)

        def submit() -> None:
            start.wait()
            try:
                outcomes.append(loads.commit(draft.draft_id, OWNER, request).created)
            except DraftBusyError as exc:
                outcomes.append(exc.code)

        threads = [threading.Thread(target=submit) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert True in outcomes
        assert set(outcomes) <= {True, False, "draft_busy"}
        with clean.connect() as connection:
            runs = connection.execute(
                text(
                    "SELECT count(*) FROM bronze.ingestion_runs"
                    " WHERE parameters ->> 'draft_id' = :d"
                ),
                {"d": str(draft.draft_id)},
            ).scalar_one()
        assert runs == 1
        again = loads.commit(draft.draft_id, OWNER, request)
        assert not again.created

    # ------------------------------------------------------------------
    # One transaction
    # ------------------------------------------------------------------
    def test_a_failure_after_the_rows_landed_leaves_everything_as_it_was(
        self, loads: LoadService, drafts: DraftService, clean: Engine
    ) -> None:
        draft = self.validated(drafts, lines_good())
        before = layers_snapshot(clean)
        with clean.begin() as connection:
            connection.execute(
                text(
                    "CREATE FUNCTION bronze.test_refuse_completion() RETURNS trigger"
                    " LANGUAGE plpgsql AS $$ BEGIN"
                    " IF NEW.state = 'completed' THEN"
                    " RAISE EXCEPTION 'the test refuses it'; END IF;"
                    " RETURN NEW; END $$"
                )
            )
            connection.execute(
                text(
                    "CREATE TRIGGER test_refuse_completion BEFORE UPDATE ON bronze.ingestion_runs"
                    " FOR EACH ROW EXECUTE FUNCTION bronze.test_refuse_completion()"
                )
            )
        try:
            with pytest.raises(EnergyError):
                self.confirm(loads, draft)
        finally:
            with clean.begin() as connection:
                connection.execute(
                    text("DROP TRIGGER test_refuse_completion ON bronze.ingestion_runs")
                )
                connection.execute(text("DROP FUNCTION bronze.test_refuse_completion()"))

        assert layers_snapshot(clean) == before
        assert drafts.get(draft.draft_id, OWNER).status is DraftStatus.VALIDATED
        assert staged_rows(clean, draft.draft_id) == 2

    def test_a_commit_killed_in_the_middle_leaves_everything_as_it_was(
        self, loads: LoadService, drafts: DraftService, clean: Engine
    ) -> None:
        draft = self.validated(drafts, lines_good())
        before = layers_snapshot(clean)
        with clean.begin() as connection:
            connection.execute(
                text(
                    "CREATE FUNCTION bronze.test_hold_completion() RETURNS trigger"
                    " LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(60); RETURN NEW; END $$"
                )
            )
            connection.execute(
                text(
                    "CREATE TRIGGER test_hold_completion BEFORE UPDATE ON bronze.ingestion_runs"
                    " FOR EACH ROW EXECUTE FUNCTION bronze.test_hold_completion()"
                )
            )
        failures: list[BaseException] = []

        def commit() -> None:
            try:
                self.confirm(loads, draft)
            except BaseException as exc:
                failures.append(exc)

        worker = threading.Thread(target=commit)
        try:
            worker.start()
            pid = None
            for _ in range(300):
                with clean.connect() as connection:
                    pid = connection.execute(
                        text(
                            "SELECT pid FROM pg_stat_activity"
                            " WHERE query LIKE '%UPDATE bronze.ingestion_runs%'"
                            " AND state = 'active' AND pid <> pg_backend_pid()"
                        )
                    ).scalar_one_or_none()
                if pid is not None:
                    break
                time.sleep(0.1)
            assert pid is not None, "the commit never reached the completion of its run"
            with clean.connect() as connection:
                connection.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})
            worker.join(timeout=60)
        finally:
            with clean.begin() as connection:
                connection.execute(
                    text("DROP TRIGGER test_hold_completion ON bronze.ingestion_runs")
                )
                connection.execute(text("DROP FUNCTION bronze.test_hold_completion()"))

        assert failures, "the killed commit must fail"
        assert layers_snapshot(clean) == before
        assert drafts.get(draft.draft_id, OWNER).status is DraftStatus.VALIDATED
        assert staged_rows(clean, draft.draft_id) == 2

    # ------------------------------------------------------------------
    # The command line and the commit
    # ------------------------------------------------------------------
    def test_the_command_line_and_the_commit_land_a_file_the_same_way(
        self,
        loads: LoadService,
        drafts: DraftService,
        storage: MinioObjectStorage,
        catalogue: DatasetCatalogue,
        clean: Engine,
    ) -> None:
        data, _expected = invoices_good(catalogue.get("qad_facturas"))
        loader = BronzeLoader(
            CalamineWorkbookReader(), storage, PostgresBronzeLandingRepository(clean), BUCKET
        )
        cli = loader.load(
            LoadRequest(
                source_key="qad_facturas",
                filename="Facturas octubre.xlsx",
                data=data,
                uploaded_by=OWNER,
            )
        )
        by_cli = self._what_landed(clean, cli.run_id)
        # Bronze has it now, so the commit would be refused: take it away again.
        with clean.begin() as connection:
            connection.execute(text("DELETE FROM bronze.raw_facturas"))
            connection.execute(text("DELETE FROM bronze.ingestion_runs"))
            connection.execute(text("DELETE FROM bronze.raw_files"))

        outcome = self.confirm(
            loads, self.validated(drafts, data, "qad_facturas", "Facturas octubre.xlsx")
        )

        by_app = self._what_landed(clean, outcome.load.summary.run_id)
        assert by_app == by_cli

    @staticmethod
    def _what_landed(engine: Engine, run_id: object) -> dict[str, Any]:
        """Everything a load records that does not depend on when or by which path it ran."""
        cli_keys = (
            "filename",
            "content_hash",
            "sheets",
            "plant_code",
            "force",
            "window_start",
            "window_end",
            "uploaded_by",
            "info",
        )
        with engine.connect() as connection:
            run = connection.execute(
                text("SELECT * FROM bronze.ingestion_runs WHERE run_id = :r"), {"r": run_id}
            ).one()
            archived = connection.execute(text("SELECT * FROM bronze.raw_files")).one()
            rows = connection.execute(
                text(
                    "SELECT source_row, content_hash, payload, sheet_name, plant_code,"
                    " source_system, source_uri FROM bronze.raw_facturas"
                    " WHERE ingestion_run_id = :r ORDER BY source_row"
                ),
                {"r": run_id},
            ).all()
        return {
            "run": (
                run.flow_name,
                run.source_key,
                run.source_system,
                run.plant_code,
                run.state,
                run.window_start,
                run.window_end,
                run.qad_program,
                run.qad_exploration,
                run.qad_user,
                run.queried_at,
                run.search_conditions,
                run.row_count,
                run.error,
            ),
            "parameters": {key: run.parameters.get(key) for key in cli_keys},
            "file": (
                archived.content_hash,
                archived.bucket,
                archived.object_key,
                archived.filename,
                archived.content_type,
                archived.size_bytes,
                archived.plant_code,
                archived.source_key,
                list(archived.sheet_names),
                archived.uploaded_by,
            ),
            "rows": [tuple(row) for row in rows],
        }

    # ------------------------------------------------------------------
    # Catalogues
    # ------------------------------------------------------------------
    def _load_lines_from_the_command_line(
        self, engine: Engine, storage: MinioObjectStorage, data: bytes
    ) -> object:
        loader = BronzeLoader(
            CalamineWorkbookReader(), storage, PostgresBronzeLandingRepository(engine), BUCKET
        )
        result = loader.load(
            LoadRequest(
                source_key="manual_sheets",
                filename="EQUS BATT.xlsx",
                data=data,
                sheets=(SheetSpec(LINES_SHEET, "lineas_produccion"),),
            )
        )
        return result.run_id

    def test_a_catalogue_is_compared_with_its_current_version_in_sql(
        self,
        loads: LoadService,
        drafts: DraftService,
        storage: MinioObjectStorage,
        clean: Engine,
    ) -> None:
        current = self._load_lines_from_the_command_line(clean, storage, lines_catalogue())
        data, expected = lines_catalogue_changed()
        draft = self.validated(drafts, data)

        shown = loads.confirmation(draft.draft_id, OWNER)
        added = loads.difference(draft.draft_id, OWNER, RowChange.ADDED, 0, 10)
        changed = loads.difference(draft.draft_id, OWNER, RowChange.CHANGED, 0, 10)
        removed = loads.difference(draft.draft_id, OWNER, RowChange.REMOVED, 0, 10)

        assert shown.current_version is not None
        assert shown.current_version.run_id == current
        assert shown.current_version.origin is LoadOrigin.CLI
        assert shown.current_version.rows == 4
        assert shown.difference is not None
        counts = {
            "added": shown.difference.added,
            "changed": shown.difference.changed,
            "removed": shown.difference.removed,
            "unchanged": shown.difference.unchanged,
        }
        assert counts == expected
        assert [row.key for row in added.items] == [("PLACA03",)]
        (line,) = changed.items
        assert [(c.key, c.before, c.after) for c in line.columns] == [
            ("Descripción", "Formación 1", "Formación uno")
        ]
        assert [(row.key, row.previous_source_row) for row in removed.items] == [(("MONT04",), 4)]

        outcome = self.confirm(loads, draft)

        assert outcome.load.parameters["difference"]["base_run_id"] == str(current)  # type: ignore[index]
        assert loads.confirmation(draft.draft_id, OWNER).draft.status is DraftStatus.COMMITTED

    def test_two_versions_of_a_catalogue_confirmed_at_once_do_not_both_land(
        self, loads: LoadService, drafts: DraftService, clean: Engine
    ) -> None:
        first = self.validated(drafts, lines_catalogue())
        second = self.validated(drafts, lines_good())
        requests = {
            draft.draft_id: CommitRequest(
                validation_digest=loads.confirmation(draft.draft_id, OWNER).validation_digest or ""
            )
            for draft in (first, second)
        }
        outcomes: list[object] = []
        start = threading.Barrier(2)

        def submit(draft_id: object) -> None:
            start.wait()
            try:
                outcomes.append(loads.commit(draft_id, OWNER, requests[draft_id]).created)  # type: ignore[arg-type]
            except CurrentVersionChangedError as exc:
                outcomes.append(exc.code)

        threads = [threading.Thread(target=submit, args=(d,)) for d in requests]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sorted(map(str, outcomes)) == ["True", "current_version_changed"]
        with clean.connect() as connection:
            runs = connection.execute(
                text("SELECT count(*) FROM bronze.ingestion_runs")
            ).scalar_one()
        assert runs == 1

    def test_a_draft_validated_by_another_spec_is_validated_again_before_it_lands(
        self, loads: LoadService, drafts: DraftService, clean: Engine
    ) -> None:
        draft = self.validated(drafts, lines_good())
        stale = loads.confirmation(draft.draft_id, OWNER).validation_digest
        with clean.begin() as connection:
            connection.execute(
                text(
                    "UPDATE staging.drafts SET spec_digest = :s, sheet_names = NULL"
                    " WHERE draft_id = :d"
                ),
                {"s": "e" * 64, "d": draft.draft_id},
            )

        with pytest.raises(ValidationChangedError):
            loads.commit(draft.draft_id, OWNER, CommitRequest(validation_digest="0" * 64))

        with clean.connect() as connection:
            kept = connection.execute(
                text("SELECT sheet_names FROM staging.drafts WHERE draft_id = :d"),
                {"d": draft.draft_id},
            ).scalar_one()
        assert kept == [LINES_SHEET]
        assert self.confirm(loads, draft).created
        assert stale is not None

    # ------------------------------------------------------------------
    # After the commit: the purge and the history
    # ------------------------------------------------------------------
    def test_the_purge_keeps_a_committed_original(
        self,
        loads: LoadService,
        drafts: DraftService,
        storage: MinioObjectStorage,
        clean: Engine,
    ) -> None:
        draft = self.validated(drafts, lines_good())
        self.confirm(loads, draft)
        bronze = PostgresBronzeLandingRepository(clean)

        def landed(content_hash: str, bucket: str, object_key: str) -> bool:
            known = bronze.find_file(content_hash)
            return known is not None and (known.bucket, known.object_key) == (bucket, object_key)

        outcome = drafts.purge(landed=landed, only=draft.draft_id)

        assert (outcome.drafts, outcome.objects) == (1, 0)
        assert storage.exists(BUCKET, draft.object_key or "")

    def test_the_history_reads_every_load(
        self,
        loads: LoadService,
        drafts: DraftService,
        storage: MinioObjectStorage,
        clean: Engine,
    ) -> None:
        cli = self._load_lines_from_the_command_line(clean, storage, lines_catalogue())
        data = lines_good()
        draft = self.validated(drafts, data)
        app = self.confirm(loads, draft).load.summary.run_id
        everyone = LoadFilter(plant_scope=PLANTS)

        page = loads.loads(everyone, LoadSort.STARTED_AT, True, 0, 25)
        mine = loads.loads(
            LoadFilter(plant_scope=PLANTS, uploaded_by=OWNER), LoadSort.ROWS, False, 0, 25
        )
        elsewhere = loads.loads(LoadFilter(plant_scope=("9999",)), LoadSort.STARTED_AT, True, 0, 25)
        view = loads.load(app, PLANTS, OWNER)
        original, stream = loads.original(app, PLANTS)
        with stream:
            downloaded = stream.read()

        assert page.total == 2
        assert {item.run_id: item.origin for item in page.items} == {
            cli: LoadOrigin.CLI,
            app: LoadOrigin.APP,
        }
        assert [item.run_id for item in mine.items] == [app]
        assert elsewhere.total == 0
        assert [(p.subject, p.name) for p in page.people] == [(OWNER, "Ada Lovelace")]
        assert view.draft_available
        assert [(s.name, s.dataset_key, s.rows) for s in view.record.summary.sheets] == [
            (LINES_SHEET, "lineas_produccion", 2)
        ]
        assert downloaded == data
        assert original.content_hash == hashlib.sha256(data).hexdigest()
        assert loads.last_loads(PLANTS)["lineas_produccion"].run_id == app
