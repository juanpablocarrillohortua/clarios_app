"""Application service for drafts: receive a file, validate it, list its issues.

Layer: services

Responsibility:
    Owns the draft use cases of the web app's loading page. The ordering is
    this module's main responsibility:

    * Everything that can be refused before a byte is read is refused first:
      an unknown dataset, a spec that takes no uploads, a name that is not an
      ``.xlsx``, a size over the limit. Nothing is recorded for those.
    * The draft is recorded (``receiving``) before its bytes arrive, so an
      upload broken off by a crash leaves a trace the purge removes.
    * The bytes go straight to object storage, a part at a time, under a
      temporary key, watched by :class:`~energy.domain.drafts.UploadGuard`.
      Once the hash is known, the file is copied to the key that carries it,
      the key the Bronze loader archives the same file under, and the
      temporary object is removed. The same bytes uploaded again for the same
      dataset by the same person return the draft they already have.
    * Any failure removes what was written and marks the draft ``failed``
      with the reason, before the error propagates.

    Validation reads the archived original back into a temporary file,
    checking its hash, inspects the archive's limits, then checks the spec's
    sheet row by row, staging the rows and storing the issues as it goes, in
    one transaction that replaces any earlier validation of the draft. A file
    problem is a result, not a failure: it is stored as an issue and the
    draft is validated, with an error. A file over
    :data:`~energy.domain.drafts.CHECK_IN_REQUEST_MAX_BYTES` is not validated
    here: that is the worker's job, in increment 12.

    Every issue comes with its likely reasons and an Excel fix (increment 6).
    Data is corrected in Excel, not in the app (ADR 0009): a corrected file
    is uploaded as a correction of the draft it came from, and becomes a
    draft of its own linked to it. Validating a correction first makes sure
    both files are judged by the same spec: if the earlier one was validated
    against another, it is validated again. The comparison then says which
    issues were cleared, which remain and which are new
    (:mod:`energy.domain.draft_comparison`).

    Nothing here writes to Bronze, Silver or Gold.

Depends on:
    ``energy.domain``, ``energy.ports.draft_repository``,
    ``energy.ports.object_storage``, ``energy.ports.workbook_reader``,
    ``energy.ports.workbook_package``, ``energy.core``.
"""

from __future__ import annotations

import contextlib
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final

from energy.core.logging import get_logger
from energy.domain.dataset_spec import spec_digest
from energy.domain.draft_comparison import (
    Comparison,
    ComparisonState,
    PreviousFile,
    compare,
)
from energy.domain.draft_rules import (
    DEFAULT_LISTED_PER_COLUMN,
    IssueCollector,
    check_sheet,
    sheet_issue,
)
from energy.domain.drafts import (
    CHECK_IN_REQUEST_MAX_BYTES,
    INCOMING_PREFIX,
    MAX_FILE_BYTES,
    XLSX_MEDIA_TYPE,
    XLSX_SUFFIX,
    DraftStatus,
    ExportFacts,
    UploadGuard,
    ValidationSummary,
    check_declared_size,
    clean_filename,
    incoming_key,
    require_xlsx,
)
from energy.domain.exceptions import (
    DraftBusyError,
    EnergyError,
    UnknownDraftError,
    UnsupportedFileFormatError,
)
from energy.domain.landing import DEFAULT_PLANT_CODE, DateSpan, archive_object_key, landing_source
from energy.domain.qad_export import (
    CONDITIONS_SHEET,
    INFO_SHEET,
    info_dictionary,
    search_conditions,
)
from energy.domain.workbook_limits import IN_REQUEST, package_issue, unreadable
from energy.ports.draft_repository import ArchivedFile, IssueFilter, NewDraft

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from uuid import UUID

    from energy.domain.dataset_spec import DatasetCatalogue, DatasetSpec
    from energy.domain.draft_rules import Issue
    from energy.domain.drafts import Draft
    from energy.domain.landing import LandingRow
    from energy.domain.workbook_limits import PackageLimits
    from energy.ports.draft_repository import DraftRepository, DraftValidation, IssuePage
    from energy.ports.object_storage import ByteSource, ObjectStorage
    from energy.ports.workbook_package import WorkbookPackageInspector
    from energy.ports.workbook_reader import OpenWorkbook, WorkbookFileReader

logger = get_logger(__name__)

#: How many listed issues wait in memory before they are written.
ISSUES_PER_WRITE: Final = 5_000
#: An upload that never finished is purged after this long.
UNFINISHED_UPLOAD_AGE: Final = timedelta(days=1)


def _utc_now() -> datetime:
    """Return the current moment in UTC."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class UploadOutcome:
    """What became of one upload.

    Attributes:
        draft: the draft the file belongs to.
        created: ``False`` when the same person had already uploaded the same
            bytes for the same dataset, and ``draft`` is that earlier draft.
    """

    draft: Draft
    created: bool


@dataclass(frozen=True, slots=True)
class PurgeOutcome:
    """What a purge removed, or would remove.

    Attributes:
        drafts: drafts removed.
        objects: archived originals removed, because nothing else needs them.
        incoming: temporary objects of uploads that never finished.
    """

    drafts: int
    objects: int
    incoming: int


class _GuardedSource:
    """A byte source whose every chunk is shown to an upload guard.

    Args:
        source: where the bytes come from.
        guard: what watches them.
    """

    def __init__(self, source: ByteSource, guard: UploadGuard) -> None:
        self._source = source
        self._guard = guard

    def read(self, size: int = -1, /) -> bytes:
        """Return up to ``size`` bytes, refusing them as soon as the guard does."""
        chunk = self._source.read(size)
        if chunk:
            self._guard.observe(chunk)
        return chunk


class DraftService:
    """Receives, validates and lists the drafts of the web app.

    Args:
        repository: the drafts in staging.
        storage: where originals are archived.
        reader: opens a workbook kept in a local file.
        inspector: reads and verifies a workbook's archive.
        catalogue: the dataset specs.
        bucket: the bucket originals are archived into.
        clock: returns the current UTC moment; replaceable in tests.
        max_file_bytes: the largest upload accepted.
        check_max_bytes: the largest file validated here.
        package_limits: what a validated file's archive may hold.
        listed_per_column: how many issues of one rule are listed per column.
        temp_dir: where validation copies the original; the system's default
            when ``None``.
    """

    def __init__(
        self,
        repository: DraftRepository,
        storage: ObjectStorage,
        reader: WorkbookFileReader,
        inspector: WorkbookPackageInspector,
        catalogue: DatasetCatalogue,
        bucket: str,
        clock: Callable[[], datetime] = _utc_now,
        max_file_bytes: int = MAX_FILE_BYTES,
        check_max_bytes: int = CHECK_IN_REQUEST_MAX_BYTES,
        package_limits: PackageLimits = IN_REQUEST,
        listed_per_column: int = DEFAULT_LISTED_PER_COLUMN,
        temp_dir: Path | None = None,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._reader = reader
        self._inspector = inspector
        self._catalogue = catalogue
        self._bucket = bucket
        self._clock = clock
        self._max_file_bytes = max_file_bytes
        self._check_max_bytes = check_max_bytes
        self._package_limits = package_limits
        self._listed_per_column = listed_per_column
        self._temp_dir = temp_dir

    # -------------------------------------------------------------------------
    # Upload
    # -------------------------------------------------------------------------
    def open_upload(
        self,
        dataset_key: str,
        filename: str,
        content_type: str,
        declared_bytes: int | None,
        owner: str,
        owner_name: str | None,
        previous_draft_id: UUID | None = None,
    ) -> Draft:
        """Check what an upload announces and record its draft.

        Args:
            dataset_key: the dataset spec the file is for.
            filename: the file's name, as the client sent it.
            content_type: the content type the client declared.
            declared_bytes: the size the upload announced.
            owner: the person, as the identity provider's subject.
            owner_name: their name, for display.
            previous_draft_id: for a correction, the draft it corrects.

        Returns:
            The draft, in state ``receiving``.

        Raises:
            UnknownDatasetError: if no spec has that key.
            InvalidFilenameError: if the name is unusable.
            UnsupportedFileFormatError: if the name or type is not an ``.xlsx``.
            LengthRequiredError: if the size is not announced.
            EmptyFileError: if it is zero.
            FileTooLargeError: if it is over the limit.
        """
        spec = self._catalogue.get(dataset_key)
        cleaned = clean_filename(filename)
        media_type = content_type.split(";", 1)[0]
        require_xlsx(cleaned, media_type)
        size = check_declared_size(declared_bytes, self._max_file_bytes)
        return self._repository.create(
            NewDraft(
                dataset_key=spec.key,
                source_key=spec.bronze.source_key,
                plant_code=DEFAULT_PLANT_CODE,
                filename=cleaned,
                content_type=media_type.strip().lower() or XLSX_MEDIA_TYPE,
                declared_bytes=size,
                created_by=owner,
                created_by_name=owner_name,
                previous_draft_id=previous_draft_id,
            )
        )

    def open_correction(
        self,
        draft_id: UUID,
        filename: str,
        content_type: str,
        declared_bytes: int | None,
        owner: str,
        owner_name: str | None,
    ) -> Draft:
        """Record the draft of a corrected file, for the dataset of the draft it corrects.

        Args:
            draft_id: the draft whose file was corrected.
            filename: the corrected file's name, as the client sent it.
            content_type: the content type the client declared.
            declared_bytes: the size the upload announced.
            owner: the person, who must own the earlier draft.
            owner_name: their name, for display.

        Returns:
            The new draft, in state ``receiving``, linked to the earlier one.

        Raises:
            UnknownDraftError: if ``owner`` has no live draft of that ID.
            DraftBusyError: if the earlier draft's file never arrived.
            InvalidFilenameError, UnsupportedFileFormatError,
            LengthRequiredError, EmptyFileError, FileTooLargeError: as for
            any upload.
        """
        earlier = self.get(draft_id, owner)
        if earlier.status not in {DraftStatus.UPLOADED, DraftStatus.VALIDATED}:
            raise DraftBusyError("the draft's file never arrived", draft_id=str(draft_id))
        return self.open_upload(
            earlier.dataset_key,
            filename,
            content_type,
            declared_bytes,
            owner,
            owner_name,
            previous_draft_id=earlier.draft_id,
        )

    def receive(self, draft: Draft, body: ByteSource) -> UploadOutcome:
        """Archive the bytes of a draft as they arrive.

        Args:
            draft: the draft :meth:`open_upload` recorded.
            body: the upload's bytes, read until it returns ``b""``.

        Returns:
            The uploaded draft, or the earlier draft of the same bytes.

        Raises:
            FileTooLargeError: at the first byte past the limit.
            UnsupportedFileFormatError: if the bytes are not a zip archive.
            IncompleteUploadError: if fewer or more bytes arrive than announced.
            UploadAbortedError: if the client went away.
            SourceUnavailableError: if object storage cannot be reached.
        """
        guard = UploadGuard(draft.declared_bytes, self._max_file_bytes)
        temporary = incoming_key(draft.draft_id)
        try:
            self._storage.ensure_bucket(self._bucket)
            stored = self._storage.put_stream(
                self._bucket,
                temporary,
                _GuardedSource(body, guard),
                XLSX_MEDIA_TYPE,
                metadata={"draft-id": str(draft.draft_id)},
            )
            guard.finish()
            return self._archive(draft, temporary, stored.content_hash, stored.size_bytes)
        except BaseException as exc:
            self._abandon(draft, temporary, exc)
            raise

    def _archive(self, draft: Draft, temporary: str, content_hash: str, size: int) -> UploadOutcome:
        """Move a complete upload to the key that carries its hash."""
        earlier = self._repository.find_duplicate(
            draft.created_by, draft.dataset_key, content_hash, excluding=draft.draft_id
        )
        if earlier is not None:
            self._storage.delete(self._bucket, temporary)
            self._repository.remove([draft.draft_id])
            logger.info(
                "upload matched an earlier draft",
                draft_id=str(earlier.draft_id),
                content_hash=content_hash,
            )
            return UploadOutcome(earlier, created=False)

        key = archive_object_key(
            draft.plant_code, draft.source_key, content_hash, XLSX_SUFFIX, self._clock().date()
        )
        if not self._storage.exists(self._bucket, key):
            # Another upload of the same bytes may have archived them a moment ago.
            with contextlib.suppress(FileExistsError):
                self._storage.copy(
                    self._bucket,
                    temporary,
                    key,
                    metadata={
                        "content-hash": content_hash,
                        "plant-code": draft.plant_code,
                        "source-key": draft.source_key,
                    },
                )
        self._storage.delete(self._bucket, temporary)
        uploaded = self._repository.mark_uploaded(
            draft.draft_id, ArchivedFile(self._bucket, key, size, content_hash)
        )
        logger.info(
            "upload archived",
            draft_id=str(draft.draft_id),
            dataset_key=draft.dataset_key,
            size_bytes=size,
            content_hash=content_hash,
        )
        return UploadOutcome(uploaded, created=True)

    def _abandon(self, draft: Draft, temporary: str, cause: BaseException) -> None:
        """Remove what a failed upload left and record why it failed."""
        code = cause.code if isinstance(cause, EnergyError) else "internal_error"
        try:
            self._storage.delete(self._bucket, temporary)
        except EnergyError:
            logger.warning("could not remove a failed upload", draft_id=str(draft.draft_id))
        try:
            self._repository.mark_failed(draft.draft_id, code)
        except EnergyError:
            logger.exception("could not mark the upload failed", draft_id=str(draft.draft_id))
        logger.info("upload failed", draft_id=str(draft.draft_id), failure_code=code)

    # -------------------------------------------------------------------------
    # Reading and validating
    # -------------------------------------------------------------------------
    def get(self, draft_id: UUID, owner: str) -> Draft:
        """Return a live draft of ``owner``.

        Raises:
            UnknownDraftError: if there is none of that ID.
        """
        draft = self._repository.find(draft_id, owner)
        if draft is None:
            raise UnknownDraftError("no draft of yours has that ID", draft_id=str(draft_id))
        return draft

    def validate(self, draft_id: UUID, owner: str) -> Draft:
        """Check a draft's file against its dataset spec, replacing earlier results.

        For a correction, the earlier draft is validated again afterwards if
        it was judged by another spec, so the comparison judges both files by
        the same rules. If that cannot be done (the earlier file is too large
        to validate here, or busy), the comparison says the rules changed.

        Args:
            draft_id: the draft.
            owner: the person asking.

        Returns:
            The validated draft, with what was found.

        Raises:
            UnknownDraftError: if ``owner`` has no live draft of that ID.
            DraftBusyError: if its file has not arrived, or another validation
                of it is running.
            NotImplementedError: if the file is over the size validated here.
        """
        validated = self._validate_one(self.get(draft_id, owner), owner)
        earlier = self._earlier(validated, owner)
        if earlier is not None and self._judged_by_another_spec(earlier, validated):
            with contextlib.suppress(DraftBusyError, NotImplementedError):
                self._validate_one(earlier, owner)
                logger.info(
                    "earlier draft validated again under the current spec",
                    draft_id=str(earlier.draft_id),
                    correction=str(draft_id),
                )
        return validated

    def _validate_one(self, draft: Draft, owner: str) -> Draft:
        """Validate one draft's file; see :meth:`validate`."""
        if draft.status not in {DraftStatus.UPLOADED, DraftStatus.VALIDATED}:
            raise DraftBusyError("the draft's file has not arrived", draft_id=str(draft.draft_id))
        if (draft.size_bytes or 0) > self._check_max_bytes:
            msg = "Increment 12: files over 5 MB are validated by a job, which is not built yet"
            raise NotImplementedError(msg)
        spec = self._catalogue.get(draft.dataset_key)
        bucket, key = draft.bucket or self._bucket, draft.object_key or ""

        with (
            self._repository.validation(draft.draft_id, owner) as writing,
            tempfile.TemporaryDirectory(prefix="energy-draft-", dir=self._temp_dir) as folder,
        ):
            path = Path(folder) / "original.xlsx"
            with path.open("wb") as target:
                self._storage.download_to(bucket, key, target)
            summary = self._check(spec, path, writing)
            validated = writing.finish(summary)

        logger.info(
            "draft validated",
            draft_id=str(draft.draft_id),
            rows_read=summary.rows_read,
            rows_with_errors=summary.rows_with_errors,
            blocking=summary.blocking,
        )
        return validated

    def _earlier(self, draft: Draft, owner: str) -> Draft | None:
        """Return the live draft a correction corrects, or ``None``."""
        if draft.previous_draft_id is None:
            return None
        return self._repository.find(draft.previous_draft_id, owner)

    def _judged_by_another_spec(self, earlier: Draft, correction: Draft) -> bool:
        """Return whether two drafts' last validations used different specs."""
        if earlier.validation is None or correction.validation is None:
            return earlier.validation is None
        return earlier.validation.spec_digest != correction.validation.spec_digest

    def comparison(self, draft: Draft, owner: str) -> Comparison | None:
        """Return how a correction compares with the file it corrects.

        Args:
            draft: a draft of ``owner``.
            owner: the person asking.

        Returns:
            ``None`` for a draft that corrects nothing; otherwise the
            comparison, or the reason there is none.
        """
        if draft.previous_draft_id is None:
            return None
        earlier = self._earlier(draft, owner)
        if earlier is None:
            return Comparison(ComparisonState.PREVIOUS_GONE, previous=None)
        previous = PreviousFile(earlier.draft_id, earlier.filename, earlier.validated_at)
        if draft.validation is None:
            return Comparison(ComparisonState.NOT_VALIDATED, previous)
        if earlier.validation is None:
            return Comparison(ComparisonState.PREVIOUS_NOT_VALIDATED, previous)
        if self._judged_by_another_spec(earlier, draft):
            return Comparison(ComparisonState.RULES_CHANGED, previous)
        revalidated = (
            earlier.validated_at is not None
            and draft.validated_at is not None
            # Equal only when both ran in one request, a clock tick apart.
            and earlier.validated_at >= draft.validated_at
        )
        return compare(
            earlier.validation.counts,
            draft.validation.counts,
            self._repository.matched(draft.draft_id, earlier.draft_id),
            previous,
            previous_revalidated=revalidated,
        )

    def _comparable(self, draft: Draft, owner: str) -> Draft | None:
        """Return the earlier draft when a correction's issues can be matched with it."""
        compared = self.comparison(draft, owner)
        if compared is None or compared.state is not ComparisonState.READY:
            return None
        return self._earlier(draft, owner)

    def _check(self, spec: DatasetSpec, path: Path, writing: DraftValidation) -> ValidationSummary:
        """Check one file against its spec, staging rows and issues as it goes.

        What the workbook says about itself (its sheets, the period its rows
        cover, its Info and search-conditions sheets) is read on the way, for
        the run a commit opens: the same facts the command-line loader records.
        """
        collector = IssueCollector(self._listed_per_column)
        rows_read = 0
        facts: ExportFacts | None = None
        problem = self._package_problem(path)
        if problem is not None:
            collector.add(problem)
        else:
            try:
                workbook = self._reader.open_workbook_file(path)
                missing = sheet_issue(spec, workbook.sheet_names)
                if missing is not None:
                    collector.add(missing)
                else:
                    period_key = landing_source(spec.bronze.source_key).period_key
                    span = DateSpan(period_key) if period_key else None
                    sheet = workbook.sheet(spec.bronze.sheet)
                    rows = check_sheet(
                        spec, sheet.rows, collector, sheet.first_row, sheet.first_column, span
                    )
                    rows_read = writing.stage_rows(_flushing(rows, collector, writing))
                    facts = _export_facts(workbook, span)
            except UnsupportedFileFormatError:
                collector.add(unreadable("corrupt"))
        writing.add_issues(collector.take())
        return ValidationSummary(
            sheet_name=spec.bronze.sheet,
            header_row=spec.bronze.header_row,
            rows_read=rows_read,
            rows_with_errors=collector.rows_with_errors,
            rows_with_warnings=collector.rows_with_warnings,
            counts=collector.counts(),
            spec_digest=spec_digest(spec),
            facts=facts,
        )

    def _package_problem(self, path: Path) -> Issue | None:
        """Return the first problem of a workbook's archive, or ``None``."""
        try:
            entries = self._inspector.entries(path)
        except UnsupportedFileFormatError as exc:
            return unreadable(str(exc.details.get("reason", "corrupt")))
        problem = package_issue(entries, self._package_limits)
        if problem is not None:
            return problem
        try:
            self._inspector.verify(path)
        except UnsupportedFileFormatError:
            return unreadable("corrupt")
        return None

    def issues(
        self,
        draft_id: UUID,
        owner: str,
        wanted: IssueFilter | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> IssuePage:
        """Return a page of a draft's listed issues.

        For a correction that can be compared, each item says whether the
        earlier file had it.

        Raises:
            UnknownDraftError: if ``owner`` has no live draft of that ID.
        """
        draft = self.get(draft_id, owner)
        earlier = self._comparable(draft, owner)
        return self._repository.issues(
            draft_id,
            owner,
            wanted or IssueFilter(),
            offset,
            limit,
            previous_id=earlier.draft_id if earlier else None,
        )

    def cleared(self, draft_id: UUID, owner: str, offset: int = 0, limit: int = 100) -> IssuePage:
        """Return a page of the issues of the earlier file that a correction no longer has.

        Raises:
            UnknownDraftError: if ``owner`` has no live draft of that ID, or
                the draft is no correction that can be compared.
        """
        draft = self.get(draft_id, owner)
        earlier = self._comparable(draft, owner)
        if earlier is None:
            raise UnknownDraftError(
                "this draft corrects no file it can be compared with", draft_id=str(draft_id)
            )
        return self._repository.cleared(draft_id, earlier.draft_id, offset, limit)

    # -------------------------------------------------------------------------
    # Purge
    # -------------------------------------------------------------------------
    def purge(
        self,
        landed: Callable[[str, str, str], bool],
        dry_run: bool = False,
        only: UUID | None = None,
    ) -> PurgeOutcome:
        """Remove expired drafts, and the originals nothing else needs.

        An original stays while another draft points at it or Bronze
        catalogues that very object (``landed``): a committed draft's original
        is evidence. The same bytes archived by the command line under another
        key do not keep a draft's copy.

        Args:
            landed: says, from a file's hash, bucket and key, whether Bronze
                catalogues that object.
            dry_run: count what would go, and remove nothing.
            only: purge this one draft, expired or not, and nothing else.

        Returns:
            What was removed, or would be.
        """
        now = self._clock()
        candidates = self._repository.expired(now, only)
        ids = [candidate.draft_id for candidate in candidates]
        objects: set[str] = set()
        for candidate in candidates:
            key = candidate.object_key
            if key is None or candidate.bucket is None or key in objects:
                continue
            if self._repository.references(candidate.bucket, key, ids):
                continue
            if candidate.content_hash and landed(candidate.content_hash, candidate.bucket, key):
                continue
            objects.add(key)
            if not dry_run:
                self._storage.delete(candidate.bucket, key)

        incoming = 0
        if only is None:
            for stored in self._storage.list_objects(self._bucket, INCOMING_PREFIX):
                if now - stored.stored_at >= UNFINISHED_UPLOAD_AGE:
                    incoming += 1
                    if not dry_run:
                        self._storage.delete(self._bucket, stored.key)

        removed = len(ids) if dry_run else self._repository.remove(ids)
        logger.info(
            "drafts purged",
            drafts=removed,
            objects=len(objects),
            incoming=incoming,
            dry_run=dry_run,
        )
        return PurgeOutcome(drafts=removed, objects=len(objects), incoming=incoming)


def _export_facts(workbook: OpenWorkbook, span: DateSpan | None) -> ExportFacts:
    """Return what a workbook says about itself, as the loader reads it (``_new_run``)."""
    info: dict[str, str] = {}
    if INFO_SHEET in workbook.sheet_names:
        info = info_dictionary(workbook.sheet(INFO_SHEET).rows)
    conditions: list[dict[str, str]] = []
    if CONDITIONS_SHEET in workbook.sheet_names:
        conditions = search_conditions(workbook.sheet(CONDITIONS_SHEET).rows)
    first, last = span.bounds() if span is not None else (None, None)
    return ExportFacts(
        sheet_names=tuple(workbook.sheet_names),
        window_start=first,
        window_end=last,
        export_info=info,
        search_conditions=tuple(conditions),
    )


def _flushing(
    rows: Iterable[LandingRow], collector: IssueCollector, writing: DraftValidation
) -> Iterator[LandingRow]:
    """Pass rows on, storing the collected issues whenever enough are waiting."""
    for row in rows:
        yield row
        if collector.pending >= ISSUES_PER_WRITE:
            writing.add_issues(collector.take())


__all__ = ["ISSUES_PER_WRITE", "DraftService", "PurgeOutcome", "UploadOutcome"]
