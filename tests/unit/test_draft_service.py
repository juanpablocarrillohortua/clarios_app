"""The draft use cases, without a database (``energy.services.draft_service``).

The repository is the in-memory stand-in of ``tests/fixtures/drafts.py``; the
archive is a directory; the workbook reader and the archive inspector are the
real ones, and the specs are the committed pilots. The PostgreSQL adapter and
MinIO are checked in ``tests/integration/test_drafts_end_to_end.py``.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from tests.fixtures.drafts import InMemoryDraftRepository
from tests.fixtures.workbooks import LINES_SHEET, build_workbook, lines_broken, lines_good

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.adapters.local_storage import LocalObjectStorage
from energy.adapters.yaml_dataset_specs import load_dataset_specs
from energy.adapters.zip_package import ZipPackageInspector
from energy.domain.draft_rules import IssueSeverity, RuleId
from energy.domain.drafts import XLSX_MEDIA_TYPE, DraftStatus
from energy.domain.exceptions import (
    DraftBusyError,
    EmptyFileError,
    EntryModeNotAllowedError,
    FileTooLargeError,
    IncompleteUploadError,
    LengthRequiredError,
    UnknownDatasetError,
    UnknownDraftError,
    UnsupportedFileFormatError,
    UploadAbortedError,
)
from energy.domain.landing import landing_rows
from energy.ports.draft_repository import IssueFilter
from energy.services.draft_service import DraftService

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from energy.domain.dataset_spec import DatasetCatalogue
    from energy.domain.drafts import Draft

BUCKET = "bronze-raw"
OWNER = "00000000-0000-4000-8000-000000000001"
SOMEONE_ELSE = "00000000-0000-4000-8000-000000000002"


class Body:
    """An upload's body, handed over a few bytes at a time."""

    def __init__(self, data: bytes, chunk: int = 4096, fail_at: int | None = None) -> None:
        self._data = io.BytesIO(data)
        self._chunk = chunk
        self._fail_at = fail_at

    def read(self, size: int = -1, /) -> bytes:
        if self._fail_at is not None and self._data.tell() >= self._fail_at:
            raise UploadAbortedError("the client went away during the upload")
        return self._data.read(min(size, self._chunk) if size >= 0 else self._chunk)


@pytest.fixture(scope="module")
def catalogue(repo_root: Path) -> DatasetCatalogue:
    return load_dataset_specs(repo_root / "config")


@pytest.fixture
def repository() -> InMemoryDraftRepository:
    return InMemoryDraftRepository()


@pytest.fixture
def storage(tmp_path: Path) -> LocalObjectStorage:
    return LocalObjectStorage(tmp_path / "archive")


@pytest.fixture
def make_service(
    repository: InMemoryDraftRepository,
    storage: LocalObjectStorage,
    catalogue: DatasetCatalogue,
    tmp_path: Path,
) -> Callable[..., DraftService]:
    def make(**overrides: object) -> DraftService:
        arguments: dict[str, object] = {
            "repository": repository,
            "storage": storage,
            "reader": CalamineWorkbookReader(),
            "inspector": ZipPackageInspector(),
            "catalogue": catalogue,
            "bucket": BUCKET,
            "temp_dir": tmp_path,
            **overrides,
        }
        return DraftService(**arguments)  # type: ignore[arg-type]

    return make


@pytest.fixture
def service(make_service: Callable[..., DraftService]) -> DraftService:
    return make_service()


def upload(
    service: DraftService,
    data: bytes,
    dataset_key: str = "lineas_produccion",
    owner: str = OWNER,
    name: str = "Líneas.xlsx",
) -> Draft:
    draft = service.open_upload(dataset_key, name, XLSX_MEDIA_TYPE, len(data), owner, "Ada")
    return service.receive(draft, Body(data)).draft


class TestOpenUpload:
    def test_an_upload_is_recorded_before_its_bytes_arrive(
        self, service: DraftService, repository: InMemoryDraftRepository
    ) -> None:
        draft = service.open_upload(
            "lineas_produccion", "C:\\fakepath\\Líneas.xlsx", XLSX_MEDIA_TYPE, 10, OWNER, "Ada"
        )

        assert draft.status is DraftStatus.RECEIVING
        assert draft.filename == "Líneas.xlsx"
        assert (draft.source_key, draft.plant_code) == ("manual_sheets", "1515")
        assert repository.find(draft.draft_id, OWNER) == draft

    @pytest.mark.parametrize(
        ("dataset", "name", "media", "size", "error"),
        [
            ("nada", "a.xlsx", XLSX_MEDIA_TYPE, 10, UnknownDatasetError),
            ("lineas_produccion", "a.csv", "text/csv", 10, UnsupportedFileFormatError),
            ("lineas_produccion", "a.xlsx", "text/plain", 10, UnsupportedFileFormatError),
            ("lineas_produccion", "a.xlsx", XLSX_MEDIA_TYPE, None, LengthRequiredError),
            ("lineas_produccion", "a.xlsx", XLSX_MEDIA_TYPE, 0, EmptyFileError),
            ("lineas_produccion", "a.xlsx", XLSX_MEDIA_TYPE, 300 * 2**20 + 1, FileTooLargeError),
        ],
    )
    def test_what_is_refused_before_a_byte_is_read_leaves_no_trace(
        self,
        service: DraftService,
        repository: InMemoryDraftRepository,
        dataset: str,
        name: str,
        media: str,
        size: int | None,
        error: type[Exception],
    ) -> None:
        with pytest.raises(error):
            service.open_upload(dataset, name, media, size, OWNER, None)

        assert repository.drafts == {}

    def test_a_dataset_that_takes_no_file_is_refused(
        self, make_service: Callable[..., DraftService], catalogue: DatasetCatalogue
    ) -> None:
        import dataclasses  # noqa: PLC0415

        from energy.domain.dataset_spec import DatasetCatalogue, EntryMode  # noqa: PLC0415

        lines = dataclasses.replace(
            catalogue.get("lineas_produccion"), entry_modes=(EntryMode.GRID,)
        )
        service = make_service(catalogue=DatasetCatalogue((lines,)))

        with pytest.raises(EntryModeNotAllowedError):
            service.open_upload("lineas_produccion", "a.xlsx", XLSX_MEDIA_TYPE, 10, OWNER, None)


class TestReceive:
    def test_the_original_is_archived_under_its_hash(
        self, service: DraftService, storage: LocalObjectStorage
    ) -> None:
        data = lines_good()
        digest = hashlib.sha256(data).hexdigest()
        today = datetime.now(UTC).date()

        draft = upload(service, data)

        assert draft.status is DraftStatus.UPLOADED
        assert (draft.size_bytes, draft.content_hash) == (len(data), digest)
        assert draft.object_key == f"1515/manual_sheets/{today:%Y/%m/%d}/{digest}.xlsx"
        assert storage.get(BUCKET, draft.object_key) == data
        assert list(storage.list_objects(BUCKET, "incoming/")) == []

    @pytest.mark.parametrize(
        ("body", "declared", "error", "code"),
        [
            (b"not a zip at all", 16, UnsupportedFileFormatError, "unsupported_file_format"),
            (b"PK\x03\x04short", 100, IncompleteUploadError, "incomplete_upload"),
        ],
    )
    def test_a_failed_upload_leaves_no_object_and_says_why(
        self,
        service: DraftService,
        repository: InMemoryDraftRepository,
        storage: LocalObjectStorage,
        body: bytes,
        declared: int,
        error: type[Exception],
        code: str,
    ) -> None:
        draft = service.open_upload(
            "lineas_produccion", "a.xlsx", XLSX_MEDIA_TYPE, declared, OWNER, None
        )

        with pytest.raises(error):
            service.receive(draft, Body(body))

        failed = repository.drafts[draft.draft_id]
        assert (failed.status, failed.failure_code) == (DraftStatus.FAILED, code)
        assert list(storage.list_objects(BUCKET)) == []

    def test_counting_stops_at_the_first_byte_past_the_limit(
        self, make_service: Callable[..., DraftService], repository: InMemoryDraftRepository
    ) -> None:
        service = make_service(max_file_bytes=4096)
        draft = service.open_upload(
            "lineas_produccion", "a.xlsx", XLSX_MEDIA_TYPE, 4096, OWNER, None
        )
        oversized = b"PK\x03\x04" + bytes(8000)

        with pytest.raises(FileTooLargeError):
            service.receive(draft, Body(oversized, chunk=1000))

        assert repository.drafts[draft.draft_id].failure_code == "file_too_large"

    def test_a_client_that_goes_away_leaves_nothing(
        self,
        service: DraftService,
        repository: InMemoryDraftRepository,
        storage: LocalObjectStorage,
    ) -> None:
        data = lines_good()
        draft = service.open_upload(
            "lineas_produccion", "a.xlsx", XLSX_MEDIA_TYPE, len(data), OWNER, None
        )

        with pytest.raises(UploadAbortedError):
            service.receive(draft, Body(data, chunk=100, fail_at=300))

        assert repository.drafts[draft.draft_id].failure_code == "client_disconnected"
        assert list(storage.list_objects(BUCKET)) == []

    def test_the_same_bytes_again_return_the_earlier_draft(
        self,
        service: DraftService,
        repository: InMemoryDraftRepository,
        storage: LocalObjectStorage,
    ) -> None:
        data = lines_good()
        first = upload(service, data)
        draft = service.open_upload(
            "lineas_produccion", "otro.xlsx", XLSX_MEDIA_TYPE, len(data), OWNER, None
        )

        outcome = service.receive(draft, Body(data))

        assert (outcome.created, outcome.draft.draft_id) == (False, first.draft_id)
        assert list(repository.drafts) == [first.draft_id]
        assert len(list(storage.list_objects(BUCKET))) == 1

    def test_someone_else_with_the_same_bytes_gets_a_draft_of_their_own(
        self, service: DraftService, storage: LocalObjectStorage
    ) -> None:
        data = lines_good()
        first = upload(service, data)

        second = upload(service, data, owner=SOMEONE_ELSE)

        assert second.draft_id != first.draft_id
        assert second.object_key == first.object_key
        assert len(list(storage.list_objects(BUCKET))) == 1


class TestValidate:
    def test_a_broken_file_shows_every_failure_with_row_column_and_rule(
        self, service: DraftService
    ) -> None:
        data, manifest = lines_broken()
        draft = upload(service, data)

        validated = service.validate(draft.draft_id, OWNER)

        page = service.issues(draft.draft_id, OWNER, limit=500)
        found = [(issue.source_row, issue.column_letter, issue.rule.value) for issue in page.items]
        assert len(found) == len(manifest)
        assert set(found) == set(manifest)
        summary = validated.validation
        assert summary is not None
        assert (summary.rows_read, summary.rows_with_errors, summary.rows_with_warnings) == (
            9,
            7,
            0,
        )
        assert summary.blocking
        assert validated.status is DraftStatus.VALIDATED

    def test_the_rows_staged_are_the_rows_bronze_would_land(
        self, service: DraftService, repository: InMemoryDraftRepository
    ) -> None:
        data, _manifest = lines_broken()
        draft = upload(service, data)

        service.validate(draft.draft_id, OWNER)

        sheet = CalamineWorkbookReader().open_workbook(data).sheet(LINES_SHEET)
        assert repository.rows[draft.draft_id] == list(landing_rows(sheet.rows))

    def test_a_sound_file_is_not_blocking(self, service: DraftService) -> None:
        draft = upload(service, lines_good())

        summary = service.validate(draft.draft_id, OWNER).validation

        assert summary is not None
        assert (summary.rows_read, summary.blocking, summary.counts) == (2, False, ())

    def test_validating_again_replaces_the_results(
        self, service: DraftService, repository: InMemoryDraftRepository
    ) -> None:
        data, manifest = lines_broken()
        draft = upload(service, data)

        service.validate(draft.draft_id, OWNER)
        service.validate(draft.draft_id, OWNER)

        assert len(repository.issue_lists[draft.draft_id]) == len(manifest)
        assert len(repository.rows[draft.draft_id]) == 9

    def test_a_file_without_the_sheet_is_a_file_error(self, service: DraftService) -> None:
        draft = upload(service, build_workbook({"Hoja1": [["Línea"], ["X"]]}))

        validated = service.validate(draft.draft_id, OWNER)

        page = service.issues(draft.draft_id, OWNER)
        assert [issue.rule for issue in page.items] == [RuleId.FILE_SHEET_MISSING]
        assert validated.validation is not None
        assert validated.validation.rows_read == 0

    def test_a_zip_that_is_no_workbook_is_unreadable(self, service: DraftService) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as package:
            package.writestr("word/document.xml", "<w/>")
        draft = upload(service, buffer.getvalue())

        service.validate(draft.draft_id, OWNER)

        (issue,) = service.issues(draft.draft_id, OWNER).items
        assert (issue.rule, dict(issue.params)) == (
            RuleId.FILE_UNREADABLE,
            {"reason": "not_a_spreadsheet"},
        )

    def test_a_file_over_the_threshold_is_archived_but_not_validated(
        self, make_service: Callable[..., DraftService]
    ) -> None:
        service = make_service(check_max_bytes=1000)
        draft = upload(service, lines_good())

        with pytest.raises(NotImplementedError, match="Increment 12"):
            service.validate(draft.draft_id, OWNER)

        assert service.get(draft.draft_id, OWNER).status is DraftStatus.UPLOADED

    def test_a_draft_still_arriving_cannot_be_validated(self, service: DraftService) -> None:
        draft = service.open_upload("lineas_produccion", "a.xlsx", XLSX_MEDIA_TYPE, 10, OWNER, None)

        with pytest.raises(DraftBusyError):
            service.validate(draft.draft_id, OWNER)

    def test_a_draft_of_someone_else_does_not_exist_for_you(self, service: DraftService) -> None:
        draft = upload(service, lines_good())

        with pytest.raises(UnknownDraftError):
            service.validate(draft.draft_id, SOMEONE_ELSE)
        with pytest.raises(UnknownDraftError):
            service.get(draft.draft_id, SOMEONE_ELSE)
        with pytest.raises(UnknownDraftError):
            service.issues(draft.draft_id, SOMEONE_ELSE)
        with pytest.raises(UnknownDraftError):
            service.get(uuid4(), OWNER)


class TestIssues:
    def test_issues_are_filtered_and_paged(self, service: DraftService) -> None:
        data, _manifest = lines_broken()
        draft = upload(service, data)
        service.validate(draft.draft_id, OWNER)

        patterns = service.issues(draft.draft_id, OWNER, IssueFilter(rule=RuleId.CELL_PATTERN))
        warnings = service.issues(
            draft.draft_id, OWNER, IssueFilter(severity=IssueSeverity.WARNING)
        )
        almac = service.issues(draft.draft_id, OWNER, IssueFilter(column_key="Almac"))
        second_page = service.issues(draft.draft_id, OWNER, offset=2, limit=2)

        assert [issue.source_row for issue in patterns.items] == [4, 5, 8]
        assert [issue.rule for issue in warnings.items] == [RuleId.SCHEMA_COLUMN_MISSING]
        assert [issue.source_row for issue in almac.items] == [5]
        assert second_page.total == 10
        assert len(second_page.items) == 2


def test_a_synthetic_workbook_carries_no_time_of_building() -> None:
    """The purge and duplicate tests build one file twice and compare hashes.

    openpyxl and zipfile stamp the time to the second, so before the stamps
    were fixed, two builds that straddled a second differed (seen once, in a
    full run of the suite).
    """
    with zipfile.ZipFile(io.BytesIO(lines_good())) as package:
        times = {info.date_time for info in package.infolist()}
        core = package.read("docProps/core.xml")

    assert times == {(2026, 1, 1, 0, 0, 0)}
    assert set(re.findall(rb"\d{4}-\d{2}-\d{2}T[\d:]{8}Z", core)) == {b"2026-01-01T00:00:00Z"}


class TestPurge:
    def test_an_expired_draft_goes_with_its_original(
        self,
        service: DraftService,
        repository: InMemoryDraftRepository,
        storage: LocalObjectStorage,
    ) -> None:
        draft = upload(service, lines_good())
        repository.expire(draft.draft_id)

        outcome = service.purge(landed=lambda _hash: False)

        assert (outcome.drafts, outcome.objects) == (1, 1)
        assert repository.drafts == {}
        assert list(storage.list_objects(BUCKET)) == []

    def test_an_original_another_draft_or_bronze_needs_stays(
        self,
        service: DraftService,
        repository: InMemoryDraftRepository,
        storage: LocalObjectStorage,
    ) -> None:
        first = upload(service, lines_good())
        upload(service, lines_good(), owner=SOMEONE_ELSE)
        repository.expire(first.draft_id)
        only, _manifest = lines_broken()
        landed = upload(service, only)
        repository.expire(landed.draft_id)

        outcome = service.purge(landed=lambda content_hash: content_hash == landed.content_hash)

        assert (outcome.drafts, outcome.objects) == (2, 0)
        assert len(list(storage.list_objects(BUCKET))) == 2

    def test_a_dry_run_removes_nothing(
        self, service: DraftService, repository: InMemoryDraftRepository
    ) -> None:
        draft = upload(service, lines_good())
        repository.expire(draft.draft_id)

        outcome = service.purge(landed=lambda _hash: False, dry_run=True)

        assert (outcome.drafts, outcome.objects) == (1, 1)
        assert draft.draft_id in repository.drafts

    def test_one_draft_can_be_purged_at_once(
        self, service: DraftService, repository: InMemoryDraftRepository
    ) -> None:
        kept = upload(service, lines_good())
        data, _manifest = lines_broken()
        gone = upload(service, data)

        outcome = service.purge(landed=lambda _hash: False, only=gone.draft_id)

        assert outcome.drafts == 1
        assert list(repository.drafts) == [kept.draft_id]

    def test_an_upload_that_never_finished_is_cleared_after_a_day(
        self, make_service: Callable[..., DraftService], storage: LocalObjectStorage
    ) -> None:
        storage.put(BUCKET, f"incoming/{uuid4()}", b"PK\x03\x04half", "x/test")
        later = make_service(clock=lambda: datetime.now(UTC) + timedelta(days=2))

        outcome = later.purge(landed=lambda _hash: False)

        assert outcome.incoming == 1
        assert list(storage.list_objects(BUCKET, "incoming/")) == []


def test_nothing_on_the_drafts_path_can_write_to_bronze(repo_root: Path) -> None:
    """Bronze stays unchanged by construction: the drafts path never names it."""
    src = repo_root / "src" / "energy"
    repository = (src / "adapters" / "postgres_draft_repo.py").read_text(encoding="utf-8")
    service = (src / "services" / "draft_service.py").read_text(encoding="utf-8")
    router = (src / "api" / "routers" / "drafts.py").read_text(encoding="utf-8")

    assert "bronze." not in repository.lower()
    assert "staging." in repository
    for module in (service, router):
        assert "bronze_landing" not in module
        assert "BronzeLandingRepository" not in module
