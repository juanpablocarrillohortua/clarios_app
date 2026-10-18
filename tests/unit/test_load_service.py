"""Confirming a draft into Bronze and reading the history, without a database.

The draft and load services are the real ones. The drafts are kept by the
in-memory repository of ``tests/fixtures/drafts.py``, Bronze by the in-memory
committer and ledger of ``tests/fixtures/loads.py``, the archive is a
directory, and the workbook reader and the specs are the real ones. The
PostgreSQL adapters are held to the same promises in
``tests/integration/test_commit_end_to_end.py``.
"""

from __future__ import annotations

import dataclasses
import io
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from tests.fixtures.drafts import InMemoryDraftRepository
from tests.fixtures.loads import InMemoryBronze, build_load_service
from tests.fixtures.workbooks import (
    INVOICES_INFO,
    LINES_HEADERS,
    LINES_SHEET,
    NBSP,
    build_workbook,
    invoices_good,
    lines_broken,
    lines_catalogue,
    lines_catalogue_changed,
    lines_good,
)

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.adapters.local_storage import LocalObjectStorage
from energy.adapters.yaml_dataset_specs import load_dataset_specs
from energy.adapters.zip_package import ZipPackageInspector
from energy.domain.draft_commit import CommitBlock, LoadOrigin, RowChange, validation_digest
from energy.domain.drafts import XLSX_MEDIA_TYPE, DraftStatus
from energy.domain.exceptions import (
    AlreadyLoadedError,
    CurrentVersionChangedError,
    DatasetBusyError,
    DraftBlockedError,
    DraftBusyError,
    NotValidatedError,
    OriginalMissingError,
    UnknownDraftError,
    UnknownLoadError,
    ValidationChangedError,
    WarningsNotAcceptedError,
)
from energy.domain.landing import landing_rows
from energy.ports.load_ledger import LoadFilter, LoadSort
from energy.services.draft_service import DraftService
from energy.services.load_service import CommitRequest, LoadService, target_of

if TYPE_CHECKING:
    from tests.fixtures.loads import InMemoryLoadCommitter, InMemoryLoadLedger

    from energy.domain.dataset_spec import DatasetCatalogue, DatasetSpec
    from energy.domain.drafts import Draft

pytestmark = pytest.mark.unit

BUCKET = "bronze-raw"
OWNER = "00000000-0000-4000-8000-000000000001"
SOMEONE_ELSE = "00000000-0000-4000-8000-000000000002"
PLANTS = ("1515",)


class Body:
    """An upload's body."""

    def __init__(self, data: bytes) -> None:
        self._data = io.BytesIO(data)

    def read(self, size: int = -1, /) -> bytes:
        return self._data.read(size)


@pytest.fixture(scope="module")
def catalogue() -> DatasetCatalogue:
    return load_dataset_specs(Path(__file__).resolve().parents[2] / "config")


@pytest.fixture
def repository() -> InMemoryDraftRepository:
    return InMemoryDraftRepository()


@pytest.fixture
def storage(tmp_path: Path) -> LocalObjectStorage:
    return LocalObjectStorage(tmp_path / "archive")


@pytest.fixture
def drafts(
    repository: InMemoryDraftRepository,
    storage: LocalObjectStorage,
    catalogue: DatasetCatalogue,
    tmp_path: Path,
) -> DraftService:
    return DraftService(
        repository=repository,
        storage=storage,
        reader=CalamineWorkbookReader(),
        inspector=ZipPackageInspector(),
        catalogue=catalogue,
        bucket=BUCKET,
        temp_dir=tmp_path,
    )


@pytest.fixture
def bronze() -> InMemoryBronze:
    return InMemoryBronze()


@pytest.fixture
def wired(
    drafts: DraftService,
    repository: InMemoryDraftRepository,
    storage: LocalObjectStorage,
    catalogue: DatasetCatalogue,
    bronze: InMemoryBronze,
) -> tuple[LoadService, InMemoryLoadCommitter, InMemoryLoadLedger]:
    return build_load_service(drafts, repository, storage, catalogue, bronze)


@pytest.fixture
def loads(wired: tuple[LoadService, InMemoryLoadCommitter, InMemoryLoadLedger]) -> LoadService:
    return wired[0]


@pytest.fixture
def committer(
    wired: tuple[LoadService, InMemoryLoadCommitter, InMemoryLoadLedger],
) -> InMemoryLoadCommitter:
    return wired[1]


def validated(
    drafts: DraftService,
    data: bytes,
    dataset_key: str = "lineas_produccion",
    owner: str = OWNER,
    name: str = "Líneas.xlsx",
) -> Draft:
    """Upload a file and validate it."""
    draft = drafts.open_upload(dataset_key, name, XLSX_MEDIA_TYPE, len(data), owner, "Ada Lovelace")
    uploaded = drafts.receive(draft, Body(data)).draft
    if uploaded.status in {DraftStatus.VALIDATED, DraftStatus.COMMITTED}:
        return uploaded
    return drafts.validate(uploaded.draft_id, owner)


def confirm(loads: LoadService, draft: Draft, owner: str = OWNER, accept: bool = False):  # type: ignore[no-untyped-def]
    """Commit a draft exactly as the confirmation shows it."""
    shown = loads.confirmation(draft.draft_id, owner)
    request = CommitRequest(
        validation_digest=shown.validation_digest or "",
        accept_warnings=accept,
        base_run_id=shown.current_version.run_id if shown.current_version else None,
    )
    return loads.commit(draft.draft_id, owner, request)


def invoices(catalogue: DatasetCatalogue) -> DatasetSpec:
    return catalogue.get("qad_facturas")


def lines_with_a_warning() -> bytes:
    """A production line whose free text carries a non-breaking space: a warning only."""
    row = ["FAFORM01", "1010", "Formación", 100, "", f"Manual{NBSP}", "", "1 02:30:00", "Sí", ""]
    return build_workbook({LINES_SHEET: [LINES_HEADERS, row]})


def payloads_of(data: bytes) -> list[dict[str, str]]:
    """The payloads Bronze stores for a production-lines workbook."""
    import json  # noqa: PLC0415

    sheet = CalamineWorkbookReader().open_workbook(data).sheet(LINES_SHEET)
    return [json.loads(row.payload_json) for row in landing_rows(sheet.rows)]


# =============================================================================
# The confirmation
# =============================================================================
class TestConfirmation:
    def test_a_movement_shows_its_rows_and_period(
        self, loads: LoadService, drafts: DraftService, catalogue: DatasetCatalogue
    ) -> None:
        data, expected = invoices_good(invoices(catalogue))
        draft = validated(drafts, data, "qad_facturas")

        shown = loads.confirmation(draft.draft_id, OWNER)

        assert shown.blocked_by is None
        assert shown.rows == expected["rows"]
        assert shown.period == (date(2025, 10, 3), date(2025, 10, 31))
        assert (shown.overlapping, shown.current_version, shown.difference) == ((), None, None)
        assert shown.warnings == ()
        assert shown.validation_digest == validation_digest(draft.validation)  # type: ignore[arg-type]

    def test_a_movement_names_the_loads_that_already_cover_its_period(
        self,
        loads: LoadService,
        drafts: DraftService,
        catalogue: DatasetCatalogue,
        bronze: InMemoryBronze,
    ) -> None:
        target = target_of(invoices(catalogue))
        september = bronze.add_cli_load(
            target,
            [{"Núm Fact": "A"}],
            content_hash="1" * 64,
            window=(date(2025, 9, 1), date(2025, 9, 30)),
        )
        late_october = bronze.add_cli_load(
            target,
            [{"Núm Fact": "B"}],
            content_hash="2" * 64,
            window=(date(2025, 10, 20), date(2025, 11, 5)),
        )
        data, _expected = invoices_good(invoices(catalogue))
        draft = validated(drafts, data, "qad_facturas")

        shown = loads.confirmation(draft.draft_id, OWNER)

        assert [load.run_id for load in shown.overlapping] == [late_october]
        assert september not in {load.run_id for load in shown.overlapping}

    def test_a_catalogue_shows_how_it_differs_from_the_current_version(
        self,
        loads: LoadService,
        drafts: DraftService,
        catalogue: DatasetCatalogue,
        bronze: InMemoryBronze,
    ) -> None:
        current = bronze.add_cli_load(
            target_of(catalogue.get("lineas_produccion")), payloads_of(lines_catalogue())
        )
        data, expected = lines_catalogue_changed()
        draft = validated(drafts, data)

        shown = loads.confirmation(draft.draft_id, OWNER)

        assert shown.current_version is not None
        assert (shown.current_version.run_id, shown.current_version.rows) == (current, 4)
        assert shown.current_version.origin is LoadOrigin.CLI
        difference = shown.difference
        assert difference is not None
        assert difference.base_run_id == current
        counts = {
            "added": difference.added,
            "changed": difference.changed,
            "removed": difference.removed,
            "unchanged": difference.unchanged,
        }
        assert counts == expected

    def test_a_catalogue_without_a_current_version_adds_every_row(
        self, loads: LoadService, drafts: DraftService
    ) -> None:
        draft = validated(drafts, lines_catalogue())

        shown = loads.confirmation(draft.draft_id, OWNER)

        assert shown.current_version is None
        assert shown.difference is not None
        assert (shown.difference.base_run_id, shown.difference.added) == (None, 4)

    def test_the_rows_that_differ_are_listed_with_what_changed(
        self,
        loads: LoadService,
        drafts: DraftService,
        catalogue: DatasetCatalogue,
        bronze: InMemoryBronze,
    ) -> None:
        bronze.add_cli_load(
            target_of(catalogue.get("lineas_produccion")), payloads_of(lines_catalogue())
        )
        draft = validated(drafts, lines_catalogue_changed()[0])

        added = loads.difference(draft.draft_id, OWNER, RowChange.ADDED, 0, 10)
        changed = loads.difference(draft.draft_id, OWNER, RowChange.CHANGED, 0, 10)
        removed = loads.difference(draft.draft_id, OWNER, RowChange.REMOVED, 0, 10)

        assert [row.key for row in added.items] == [("PLACA03",)]
        assert added.items[0].values["Descripción"] == "Placas 3"
        (line,) = changed.items
        assert line.key == ("FAFORM01",)
        assert [(c.key, c.before, c.after) for c in line.columns] == [
            ("Descripción", "Formación 1", "Formación uno")
        ]
        assert [(row.key, row.source_row) for row in removed.items] == [(("MONT04",), None)]

    def test_only_a_validated_catalogue_has_a_difference(
        self, loads: LoadService, drafts: DraftService, catalogue: DatasetCatalogue
    ) -> None:
        draft = validated(drafts, invoices_good(invoices(catalogue))[0], "qad_facturas")

        with pytest.raises(UnknownDraftError):
            loads.difference(draft.draft_id, OWNER, RowChange.ADDED, 0, 10)

    @pytest.mark.parametrize(
        ("make", "block"),
        [(lambda: lines_broken()[0], CommitBlock.ERRORS)],
    )
    def test_a_draft_that_cannot_land_says_why(
        self, loads: LoadService, drafts: DraftService, make: object, block: CommitBlock
    ) -> None:
        draft = validated(drafts, make())  # type: ignore[operator]

        assert loads.confirmation(draft.draft_id, OWNER).blocked_by is block

    def test_someone_elses_draft_is_not_found(
        self, loads: LoadService, drafts: DraftService
    ) -> None:
        draft = validated(drafts, lines_good())

        with pytest.raises(UnknownDraftError):
            loads.confirmation(draft.draft_id, SOMEONE_ELSE)

    def test_a_draft_validated_before_the_commit_existed_is_validated_again(
        self, loads: LoadService, drafts: DraftService, repository: InMemoryDraftRepository
    ) -> None:
        draft = validated(drafts, lines_good())
        old = dataclasses.replace(draft.validation, facts=None)  # type: ignore[arg-type]
        repository.drafts[draft.draft_id] = dataclasses.replace(draft, validation=old)

        shown = loads.confirmation(draft.draft_id, OWNER)

        assert shown.draft.validation is not None
        assert shown.draft.validation.facts is not None
        assert shown.draft.validation.facts.sheet_names == (LINES_SHEET,)


# =============================================================================
# The commit
# =============================================================================
class TestCommit:
    def test_a_movement_lands_with_everything_its_run_records(
        self,
        loads: LoadService,
        drafts: DraftService,
        catalogue: DatasetCatalogue,
        bronze: InMemoryBronze,
        repository: InMemoryDraftRepository,
    ) -> None:
        data, expected = invoices_good(invoices(catalogue))
        draft = validated(drafts, data, "qad_facturas", name="Facturas octubre.xlsx")

        outcome = confirm(loads, draft)

        assert outcome.created
        (run,) = bronze.runs.values()
        assert outcome.load.summary.run_id == run.run_id
        assert (run.source_key, run.plant_code, run.state) == ("qad_facturas", "1515", "completed")
        assert run.flow_name == "Facturas octubre"
        assert run.row_count == expected["rows"] == outcome.load.summary.rows
        assert [row.source_row for row in run.rows] == [2, 3, 4, 5]
        assert run.rows[2].content_hash == run.rows[3].content_hash  # both copies land
        assert (str(run.window_start), str(run.window_end)) == (
            expected["window_start"],
            expected["window_end"],
        )
        assert run.qad["qad_program"] == INVOICES_INFO["Programa Exploración"]
        assert run.qad["qad_exploration"] == INVOICES_INFO["Nombre Exploración"]
        assert run.qad["qad_user"] == INVOICES_INFO["ID del Usuario"]
        assert run.qad["queried_at"] is not None
        assert len(run.qad["search_conditions"]) == expected["conditions"]  # type: ignore[arg-type]
        parameters = run.parameters
        assert parameters["input_mode"] == "upload"
        assert parameters["draft_id"] == str(draft.draft_id)
        assert parameters["uploaded_by"] == OWNER
        assert parameters["content_hash"] == draft.content_hash
        known = bronze.files[draft.content_hash]  # type: ignore[index]
        assert (known.bucket, known.object_key) == (draft.bucket, draft.object_key)
        assert (known.uploaded_by, list(known.sheet_names)) == (OWNER, expected["sheet_names"])
        committed = repository.drafts[draft.draft_id]
        assert (committed.status, committed.run_id) == (DraftStatus.COMMITTED, run.run_id)
        assert repository.rows[draft.draft_id] == []

    def test_a_draft_commits_once(
        self, loads: LoadService, drafts: DraftService, bronze: InMemoryBronze
    ) -> None:
        draft = validated(drafts, lines_good())
        first = confirm(loads, draft)

        again = loads.commit(draft.draft_id, OWNER, CommitRequest(validation_digest="0" * 64))

        assert (first.created, again.created) == (True, False)
        assert again.load.summary.run_id == first.load.summary.run_id
        assert len(bronze.runs) == 1

    def test_a_catalogue_records_how_it_differed(
        self,
        loads: LoadService,
        drafts: DraftService,
        catalogue: DatasetCatalogue,
        bronze: InMemoryBronze,
    ) -> None:
        current = bronze.add_cli_load(
            target_of(catalogue.get("lineas_produccion")), payloads_of(lines_catalogue())
        )
        draft = validated(drafts, lines_catalogue_changed()[0])

        outcome = confirm(loads, draft)

        assert outcome.load.parameters["difference"] == {
            "base_run_id": str(current),
            "added": 1,
            "changed": 1,
            "removed": 1,
            "unchanged": 2,
            "removed_rows": "kept",
        }

    def test_a_catalogue_whose_current_version_moved_is_refused(
        self,
        loads: LoadService,
        drafts: DraftService,
        catalogue: DatasetCatalogue,
        bronze: InMemoryBronze,
    ) -> None:
        draft = validated(drafts, lines_catalogue_changed()[0])
        shown = loads.confirmation(draft.draft_id, OWNER)
        bronze.add_cli_load(
            target_of(catalogue.get("lineas_produccion")), payloads_of(lines_good())
        )

        with pytest.raises(CurrentVersionChangedError):
            loads.commit(
                draft.draft_id,
                OWNER,
                CommitRequest(validation_digest=shown.validation_digest or "", base_run_id=None),
            )

        assert len(bronze.runs) == 1

    def test_a_file_already_loaded_is_refused(
        self, loads: LoadService, drafts: DraftService, bronze: InMemoryBronze
    ) -> None:
        confirm(loads, validated(drafts, lines_good()))
        again = validated(drafts, lines_good(), owner=SOMEONE_ELSE)

        assert (
            loads.confirmation(again.draft_id, SOMEONE_ELSE).blocked_by
            is CommitBlock.ALREADY_LOADED
        )
        with pytest.raises(AlreadyLoadedError):
            confirm(loads, again, owner=SOMEONE_ELSE)
        assert len(bronze.runs) == 1

    def test_the_same_bytes_uploaded_again_open_the_committed_draft(
        self, loads: LoadService, drafts: DraftService
    ) -> None:
        draft = validated(drafts, lines_good())
        confirm(loads, draft)

        again = validated(drafts, lines_good())

        assert (again.draft_id, again.status) == (draft.draft_id, DraftStatus.COMMITTED)

    def test_warnings_must_be_accepted(
        self, loads: LoadService, drafts: DraftService, bronze: InMemoryBronze
    ) -> None:
        draft = validated(drafts, lines_with_a_warning())

        with pytest.raises(WarningsNotAcceptedError):
            confirm(loads, draft)
        assert bronze.runs == {}

        outcome = confirm(loads, draft, accept=True)

        accepted = outcome.load.parameters["validation"]["accepted"]  # type: ignore[index]
        assert accepted["by"] == OWNER
        assert [(w["rule"], w["rows"]) for w in accepted["warnings"]] == [("cell.text_damaged", 1)]
        assert outcome.load.summary.warnings_accepted == 1

    def test_a_validation_other_than_the_one_confirmed_is_refused(
        self, loads: LoadService, drafts: DraftService
    ) -> None:
        draft = validated(drafts, lines_good())

        with pytest.raises(ValidationChangedError):
            loads.commit(draft.draft_id, OWNER, CommitRequest(validation_digest="0" * 64))

    def test_a_draft_judged_by_another_spec_is_validated_again_and_confirmed_anew(
        self, loads: LoadService, drafts: DraftService, repository: InMemoryDraftRepository
    ) -> None:
        draft = validated(drafts, lines_good())
        stale = dataclasses.replace(draft.validation, spec_digest="e" * 64)  # type: ignore[arg-type]
        repository.drafts[draft.draft_id] = dataclasses.replace(draft, validation=stale)

        with pytest.raises(ValidationChangedError):
            loads.commit(
                draft.draft_id, OWNER, CommitRequest(validation_digest=validation_digest(stale))
            )
        assert confirm(loads, draft).created

    @pytest.mark.parametrize(
        ("make", "error"),
        [(lambda: lines_broken()[0], DraftBlockedError)],
    )
    def test_a_draft_with_errors_is_refused(
        self, loads: LoadService, drafts: DraftService, make: object, error: type[Exception]
    ) -> None:
        draft = validated(drafts, make())  # type: ignore[operator]

        with pytest.raises(error):
            confirm(loads, draft)

    def test_a_draft_not_validated_is_refused(
        self, loads: LoadService, drafts: DraftService
    ) -> None:
        data = lines_good()
        draft = drafts.open_upload(
            "lineas_produccion", "a.xlsx", XLSX_MEDIA_TYPE, len(data), OWNER, None
        )
        drafts.receive(draft, Body(data))

        with pytest.raises(NotValidatedError):
            loads.commit(draft.draft_id, OWNER, CommitRequest(validation_digest="0" * 64))

    def test_a_draft_whose_original_is_gone_is_refused(
        self,
        loads: LoadService,
        drafts: DraftService,
        storage: LocalObjectStorage,
        bronze: InMemoryBronze,
    ) -> None:
        draft = validated(drafts, lines_good())
        storage.delete(BUCKET, draft.object_key or "")

        with pytest.raises(OriginalMissingError):
            confirm(loads, draft)
        assert bronze.runs == {}

    def test_a_draft_held_by_another_commit_is_busy(
        self, loads: LoadService, drafts: DraftService, committer: InMemoryLoadCommitter
    ) -> None:
        draft = validated(drafts, lines_good())
        committer.held_drafts.add(draft.draft_id)

        with pytest.raises(DraftBusyError):
            confirm(loads, draft)

    def test_a_dataset_held_by_another_commit_is_busy(
        self,
        loads: LoadService,
        drafts: DraftService,
        committer: InMemoryLoadCommitter,
        catalogue: DatasetCatalogue,
    ) -> None:
        draft = validated(drafts, lines_good())
        committer.held_datasets.add(target_of(catalogue.get("lineas_produccion")))

        with pytest.raises(DatasetBusyError):
            confirm(loads, draft)

    def test_only_the_owner_commits(self, loads: LoadService, drafts: DraftService) -> None:
        draft = validated(drafts, lines_good())

        with pytest.raises(UnknownDraftError):
            confirm(loads, draft, owner=SOMEONE_ELSE)

    def test_a_failure_while_landing_leaves_everything_as_it_was(
        self,
        loads: LoadService,
        drafts: DraftService,
        committer: InMemoryLoadCommitter,
        bronze: InMemoryBronze,
        repository: InMemoryDraftRepository,
    ) -> None:
        draft = validated(drafts, lines_good())
        staged = list(repository.rows[draft.draft_id])

        def fail() -> None:
            msg = "the database went away"
            raise RuntimeError(msg)

        committer.after_rows = fail
        with pytest.raises(RuntimeError):
            confirm(loads, draft)

        assert (bronze.runs, bronze.files) == ({}, {})
        assert repository.drafts[draft.draft_id].status is DraftStatus.VALIDATED
        assert repository.rows[draft.draft_id] == staged


# =============================================================================
# History
# =============================================================================
class TestHistory:
    def test_every_load_is_listed_whichever_way_it_came(
        self,
        loads: LoadService,
        drafts: DraftService,
        catalogue: DatasetCatalogue,
        bronze: InMemoryBronze,
    ) -> None:
        cli = bronze.add_cli_load(target_of(invoices(catalogue)), [{"Núm Fact": "A"}])
        app = confirm(loads, validated(drafts, lines_good())).load.summary.run_id
        everyone = LoadFilter(plant_scope=PLANTS)

        page = loads.loads(everyone, LoadSort.STARTED_AT, True, 0, 25)

        assert page.total == 2
        assert {item.run_id: item.origin for item in page.items} == {
            cli: LoadOrigin.CLI,
            app: LoadOrigin.APP,
        }
        mine = loads.loads(
            dataclasses.replace(everyone, uploaded_by=OWNER), LoadSort.ROWS, False, 0, 25
        )
        assert [item.run_id for item in mine.items] == [app]
        lines = dataclasses.replace(everyone, target=target_of(catalogue.get("lineas_produccion")))
        assert [i.run_id for i in loads.loads(lines, LoadSort.STARTED_AT, True, 0, 25).items] == [
            app
        ]
        assert [(p.subject, p.name) for p in page.people] == [(OWNER, "Ada Lovelace")]
        # A command-line load is named by the dataset whose sheet it landed.
        assert {item.run_id: item.dataset_key for item in page.items} == {
            cli: "qad_facturas",
            app: "lineas_produccion",
        }
        elsewhere = dataclasses.replace(everyone, plant_scope=("9999",))
        assert loads.loads(elsewhere, LoadSort.STARTED_AT, True, 0, 25).total == 0

    def test_a_load_says_whether_its_draft_can_be_opened(
        self, loads: LoadService, drafts: DraftService
    ) -> None:
        run_id = confirm(loads, validated(drafts, lines_good())).load.summary.run_id

        assert loads.load(run_id, PLANTS, OWNER).draft_available
        assert not loads.load(run_id, PLANTS, SOMEONE_ELSE).draft_available

    def test_a_load_of_another_plant_or_none_is_not_found(
        self, loads: LoadService, drafts: DraftService
    ) -> None:
        run_id = confirm(loads, validated(drafts, lines_good())).load.summary.run_id

        with pytest.raises(UnknownLoadError):
            loads.load(run_id, ("9999",), OWNER)
        with pytest.raises(UnknownLoadError):
            loads.load(uuid4(), PLANTS, OWNER)

    def test_the_original_is_the_file_uploaded(
        self, loads: LoadService, drafts: DraftService
    ) -> None:
        data = lines_good()
        run_id = confirm(loads, validated(drafts, data)).load.summary.run_id

        original, stream = loads.original(run_id, PLANTS)
        with stream:
            assert stream.read() == data
        assert original.size_bytes == len(data)

    def test_each_dataset_names_its_last_load(
        self, loads: LoadService, drafts: DraftService
    ) -> None:
        run_id = confirm(loads, validated(drafts, lines_good())).load.summary.run_id

        last = loads.last_loads(PLANTS)

        assert set(last) == {"lineas_produccion"}
        assert last["lineas_produccion"].run_id == run_id


def test_only_the_loader_and_the_committer_write_to_bronze() -> None:
    """Bronze is written by two adapters only: the command line's and the commit's."""
    import re  # noqa: PLC0415

    src = Path(__file__).resolve().parents[2] / "src" / "energy"
    writes = re.compile(
        r"(INSERT INTO|COPY|UPDATE|DELETE FROM)\s+(bronze\.|\{BRONZE_SCHEMA\}|\{qualified\})"
    )
    writers = {
        path.relative_to(src).as_posix()
        for path in src.rglob("*.py")
        if writes.search(path.read_text(encoding="utf-8"))
    }

    assert writers == {"adapters/postgres_bronze_repo.py", "adapters/postgres_load_committer.py"}
