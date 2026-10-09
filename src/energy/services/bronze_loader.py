"""Application service landing one workbook in Bronze.

Layer: services

Responsibility:
    Owns the landing use case end to end for the equivalent-batteries (EQUS)
    sources: check the request, archive the file, decide what is already landed,
    open a run, land the rows, and close the run. The ordering is this module's
    main responsibility:

    * Everything that can be refused is refused *before* anything is written, so
      a mistyped sheet name leaves no trace.
    * The file is archived before it is catalogued, so a crash between the two
      leaves an orphaned object rather than a catalogue entry pointing at bytes
      that were never stored.
    * The run is recorded before the rows are landed and survives their
      rollback, so a failed load is visible as a ``failed`` run with no rows.

    The service lands rows verbatim. It never parses a value, never removes a
    duplicate and never touches a row that has landed.

    It assumes nothing about which years an export holds. The period a run
    covers is the first and the last date found in its rows, recorded as the run
    completes; dates the caller states replace it. What the export claims in its
    own search conditions is stored as delivered and not used for the period,
    because at least one export states a stale one.

Depends on:
    ``energy.domain``, ``energy.ports.bronze_landing``,
    ``energy.ports.object_storage``, ``energy.ports.workbook_reader``,
    ``energy.core``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath
from typing import TYPE_CHECKING, Final

from energy.core.logging import get_logger
from energy.domain.exceptions import ConfigurationError, EnergyError, PipelineError
from energy.domain.landing import (
    DEFAULT_PLANT_CODE,
    FORBIDDEN_SHEETS,
    DateSpan,
    archive_object_key,
    is_valid_dataset_key,
    landing_rows,
    landing_source,
    sha256_hex,
)
from energy.domain.qad_export import (
    CONDITIONS_SHEET,
    INFO_SHEET,
    export_info,
    info_dictionary,
    search_conditions,
)
from energy.ports.bronze_landing import LandingTarget, NewRawFile, NewRun, SheetBatch

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from datetime import date
    from uuid import UUID

    from energy.domain.landing import LandingSource, SheetSpec
    from energy.ports.bronze_landing import BronzeLandingRepository, RawFile
    from energy.ports.object_storage import ObjectStorage
    from energy.ports.workbook_reader import OpenWorkbook, WorkbookReader

logger = get_logger(__name__)

_CONTENT_TYPES: Final = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xls": "application/vnd.ms-excel",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
}
_DEFAULT_CONTENT_TYPE: Final = "application/octet-stream"


@dataclass(frozen=True, slots=True)
class LoadRequest:
    """One workbook, or some of its sheets, to land.

    Attributes:
        source_key: which kind of export the workbook is.
        filename: the workbook's file name, kept for the catalogue.
        data: the workbook's bytes.
        sheets: the sheets to land. Empty means the source's default sheets.
        plant_code: the plant the data belongs to.
        flow_name: the name the run is recorded under; the file stem when
            ``None``.
        window_start: first date of the business period. Stating either bound
            replaces the period found in the rows.
        window_end: last date of the business period, likewise.
        force: land the sheets again even if a completed run already did.
        uploaded_by: who submitted the file.
    """

    source_key: str
    filename: str
    data: bytes
    sheets: tuple[SheetSpec, ...] = ()
    plant_code: str = DEFAULT_PLANT_CODE
    flow_name: str | None = None
    window_start: date | None = None
    window_end: date | None = None
    force: bool = False
    uploaded_by: str = "system"


@dataclass(frozen=True, slots=True)
class SheetOutcome:
    """What happened to one requested sheet.

    Attributes:
        sheet_name: the worksheet.
        dataset_key: the catalogue name, for hand-maintained sheets.
        rows: rows landed by this load; zero when the sheet was skipped.
        skipped: whether a completed run had already landed the sheet.
    """

    sheet_name: str
    dataset_key: str | None
    rows: int
    skipped: bool


@dataclass(frozen=True, slots=True)
class LoadResult:
    """The outcome of one load.

    Attributes:
        source_key: the source the workbook was landed for.
        filename: the workbook's file name.
        content_hash: SHA-256 of the workbook's bytes.
        run_id: the run that landed the rows; ``None`` when every requested
            sheet was already landed and nothing was done.
        sheets: one outcome per requested sheet, in request order.
        window_start: first date of the period the run was recorded with;
            ``None`` when no run was opened or the period is unknown.
        window_end: last date of that period, likewise.
    """

    source_key: str
    filename: str
    content_hash: str
    run_id: UUID | None
    sheets: tuple[SheetOutcome, ...]
    window_start: date | None = None
    window_end: date | None = None

    @property
    def rows_landed(self) -> int:
        """Return how many rows this load landed."""
        return sum(sheet.rows for sheet in self.sheets)

    @property
    def skipped(self) -> bool:
        """Return whether the load did nothing because all was already landed."""
        return self.run_id is None


def _utc_now() -> datetime:
    """Return the current moment in UTC."""
    return datetime.now(UTC)


class BronzeLoader:
    """Lands workbooks in the Bronze layer.

    Args:
        reader: opens workbooks.
        storage: archives the original files.
        repository: the Bronze ledgers and landing tables.
        bucket: the bucket original files are archived into.
        clock: returns the current UTC moment; replaceable in tests.
    """

    def __init__(
        self,
        reader: WorkbookReader,
        storage: ObjectStorage,
        repository: BronzeLandingRepository,
        bucket: str,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._reader = reader
        self._storage = storage
        self._repository = repository
        self._bucket = bucket
        self._clock = clock

    def load(self, request: LoadRequest) -> LoadResult:
        """Land the requested sheets of one workbook.

        Args:
            request: what to land.

        Returns:
            A :class:`LoadResult`. Sheets a completed run had already landed are
            reported as skipped; if that is all of them, no run is opened.

        Raises:
            UnknownSourceError: if the source key is not a landing source.
            UnsupportedFileFormatError: if the bytes are not a readable workbook.
            ConfigurationError: if the request names a sheet that is missing,
                forbidden, or lacks a dataset key it needs. Nothing is written.
            PipelineError: if landing fails after the run was opened. The run is
                left ``failed`` and none of its rows are landed.
        """
        source = landing_source(request.source_key)
        if request.window_start and request.window_end:
            _check_window(request.window_start, request.window_end)

        content_hash = sha256_hex(request.data)
        workbook = self._reader.open_workbook(request.data)
        specs = _resolve_sheets(source, request.sheets, workbook.sheet_names)

        raw_file = self._archive_and_register(request, source, content_hash, workbook)

        already_landed: frozenset[tuple[str, str | None]] = frozenset()
        if not request.force:
            already_landed = self._repository.landed_sheets(raw_file.raw_file_id, source.key)
        pending = tuple(
            spec for spec in specs if (spec.name, spec.dataset_key) not in already_landed
        )
        if not pending:
            logger.debug(
                "every requested sheet is already landed",
                source_key=source.key,
                filename=request.filename,
            )
            return LoadResult(
                source_key=source.key,
                filename=request.filename,
                content_hash=content_hash,
                run_id=None,
                sheets=tuple(SheetOutcome(s.name, s.dataset_key, 0, skipped=True) for s in specs),
            )

        run = self._new_run(request, source, workbook, content_hash, pending)
        run_id = self._repository.open_run(run)
        target = LandingTarget(
            run_id=run_id,
            raw_file_id=raw_file.raw_file_id,
            source_key=source.key,
            source_system=source.source_system,
            plant_code=request.plant_code,
        )
        # The caller's dates and the dates found are never mixed: stating either
        # bound turns the search off.
        span: DateSpan | None = None
        if source.period_key and not (request.window_start or request.window_end):
            span = DateSpan(source.period_key)
        try:
            counts = self._repository.land(
                target,
                self._batches(workbook, raw_file, pending, span),
                period=span.bounds if span is not None else None,
            )
        except BaseException as exc:
            self._mark_failed(run_id, exc)
            if isinstance(exc, Exception):
                raise PipelineError(
                    f"load failed and nothing was landed: {exc}",
                    run_id=str(run_id),
                    source_key=source.key,
                    filename=request.filename,
                ) from exc
            raise

        landed = {spec.name for spec in pending}
        window_start, window_end = (
            span.bounds() if span is not None else (request.window_start, request.window_end)
        )
        return LoadResult(
            source_key=source.key,
            filename=request.filename,
            content_hash=content_hash,
            run_id=run_id,
            sheets=tuple(
                SheetOutcome(
                    spec.name,
                    spec.dataset_key,
                    counts.get(spec.name, 0) if spec.name in landed else 0,
                    skipped=spec.name not in landed,
                )
                for spec in specs
            ),
            window_start=window_start,
            window_end=window_end,
        )

    def _archive_and_register(
        self,
        request: LoadRequest,
        source: LandingSource,
        content_hash: str,
        workbook: OpenWorkbook,
    ) -> RawFile:
        """Make sure the file is archived, then make sure it is catalogued."""
        suffix = PurePath(request.filename).suffix.lower()
        content_type = _CONTENT_TYPES.get(suffix, _DEFAULT_CONTENT_TYPE)

        known = self._repository.find_file(content_hash)
        if known is not None:
            # Catalogued before. Restore the bytes if the archive lost them.
            self._ensure_archived(known.bucket, known.object_key, request, source, content_type)
            return known

        object_key = archive_object_key(
            request.plant_code, source.key, content_hash, suffix, self._clock().date()
        )
        self._ensure_archived(self._bucket, object_key, request, source, content_type)
        return self._repository.register_file(
            NewRawFile(
                content_hash=content_hash,
                bucket=self._bucket,
                object_key=object_key,
                filename=request.filename,
                content_type=content_type,
                size_bytes=len(request.data),
                plant_code=request.plant_code,
                source_key=source.key,
                sheet_names=tuple(workbook.sheet_names),
                uploaded_by=request.uploaded_by,
            )
        )

    def _ensure_archived(
        self,
        bucket: str,
        object_key: str,
        request: LoadRequest,
        source: LandingSource,
        content_type: str,
    ) -> None:
        """Store the file's bytes unless they are already there."""
        self._storage.ensure_bucket(bucket)
        if self._storage.exists(bucket, object_key):
            return
        self._storage.put(
            bucket,
            object_key,
            request.data,
            content_type,
            metadata={"plant-code": request.plant_code, "source-key": source.key},
        )

    def _new_run(
        self,
        request: LoadRequest,
        source: LandingSource,
        workbook: OpenWorkbook,
        content_hash: str,
        pending: Sequence[SheetSpec],
    ) -> NewRun:
        """Describe the run, reading what the export says about itself."""
        info: dict[str, str] = {}
        if INFO_SHEET in workbook.sheet_names:
            info = info_dictionary(workbook.sheet(INFO_SHEET).rows)
        conditions: list[dict[str, str]] = []
        if CONDITIONS_SHEET in workbook.sheet_names:
            conditions = search_conditions(workbook.sheet(CONDITIONS_SHEET).rows)

        export = export_info(info)

        return NewRun(
            flow_name=request.flow_name or PurePath(request.filename).stem,
            source_key=source.key,
            source_system=source.source_system,
            plant_code=request.plant_code,
            # Only what the caller states. The period the rows cover is not
            # known until they have been read, and is recorded on completion.
            window_start=request.window_start,
            window_end=request.window_end,
            qad_program=export.program,
            qad_exploration=export.exploration,
            qad_user=export.user,
            queried_at=export.queried_at,
            search_conditions=conditions,
            parameters={
                "filename": request.filename,
                "content_hash": content_hash,
                "sheets": [
                    {
                        "name": spec.name,
                        "dataset_key": spec.dataset_key,
                        "header_row": spec.header_row,
                    }
                    for spec in pending
                ],
                "plant_code": request.plant_code,
                "force": request.force,
                "window_start": _iso(request.window_start),
                "window_end": _iso(request.window_end),
                "uploaded_by": request.uploaded_by,
                "info": info,
            },
        )

    def _batches(
        self,
        workbook: OpenWorkbook,
        raw_file: RawFile,
        pending: Sequence[SheetSpec],
        span: DateSpan | None = None,
    ) -> Iterator[SheetBatch]:
        """Yield the sheets to land, reading each one only when it is reached.

        ``span`` is shown the dated column of every sheet as its rows are read,
        so it is complete once the last sheet has been consumed.
        """
        for spec in pending:
            sheet = workbook.sheet(spec.name)
            yield SheetBatch(
                sheet_name=spec.name,
                dataset_key=spec.dataset_key,
                source_uri=f"{raw_file.bucket}://{raw_file.object_key}#{spec.name}",
                rows=landing_rows(
                    sheet.rows,
                    first_row=sheet.first_row,
                    first_column=sheet.first_column,
                    header_row=spec.header_row,
                    span=span,
                ),
            )

    def _mark_failed(self, run_id: UUID, cause: BaseException) -> None:
        """Record the failure on the run without hiding the original error."""
        message = str(cause) or type(cause).__name__
        try:
            self._repository.fail_run(run_id, message)
        except EnergyError:
            logger.exception("could not mark the run as failed", run_id=str(run_id))


def _iso(value: date | None) -> str | None:
    """Return a date as ISO text, or ``None``."""
    return value.isoformat() if value is not None else None


def _check_window(start: date, end: date) -> None:
    """Refuse a business window that ends before it starts."""
    if start > end:
        raise ConfigurationError(
            "window ends before it starts", window_start=str(start), window_end=str(end)
        )


def _resolve_sheets(
    source: LandingSource,
    requested: Sequence[SheetSpec],
    available: Sequence[str],
) -> tuple[SheetSpec, ...]:
    """Check the requested sheets against the source and the workbook.

    Args:
        source: the landing source.
        requested: the sheets the caller named; empty for the source's defaults.
        available: the sheets the workbook contains.

    Returns:
        The sheets to land.

    Raises:
        ConfigurationError: if no sheet is named and the source has no default,
            or a sheet is repeated, forbidden, missing from the workbook, or at
            odds with the source over its dataset key.
    """
    specs = tuple(requested) or source.default_sheets
    if not specs:
        raise ConfigurationError(
            "this source has no default sheets; name the sheets to land",
            source_key=source.key,
        )

    seen: set[str] = set()
    for spec in specs:
        if spec.name in seen:
            raise ConfigurationError("sheet is named twice", sheet=spec.name)
        seen.add(spec.name)

        if spec.name.casefold() in FORBIDDEN_SHEETS:
            raise ConfigurationError(
                "sheet holds Excel-calculated columns and must not be landed",
                sheet=spec.name,
            )
        if spec.name not in available:
            raise ConfigurationError(
                "workbook has no such sheet", sheet=spec.name, available=list(available)
            )
        if spec.header_row < 0:
            raise ConfigurationError("header row must not be negative", sheet=spec.name)

        if source.needs_dataset_key:
            if spec.dataset_key is None or not is_valid_dataset_key(spec.dataset_key):
                raise ConfigurationError(
                    "sheet needs a lowercase snake_case dataset key, given as SHEET=dataset_key",
                    sheet=spec.name,
                    source_key=source.key,
                )
        elif spec.dataset_key is not None:
            raise ConfigurationError(
                "this source does not take a dataset key",
                sheet=spec.name,
                source_key=source.key,
            )
    return specs


__all__ = ["BronzeLoader", "LoadRequest", "LoadResult", "SheetOutcome"]
