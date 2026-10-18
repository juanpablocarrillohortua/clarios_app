"""Application service for loads: confirm a draft into Bronze, and read the history.

Layer: services

Responsibility:
    Owns the use cases of increment 7.

    * **The confirmation.** What a commit would write: the rows, the period
      of a movement and the loads that already cover it, the current version
      of a catalogue and how the new one differs from it, the warnings to
      accept, and whether the same file was loaded already. A draft whose
      validation is stale (another spec, or validated before the commit kept
      what a workbook says about itself) is validated again first, so what is
      shown is what would land.
    * **The commit.** Everything that can be refused is refused before the
      transaction opens, and checked again once the draft is locked inside it
      (:meth:`LoadCommitter.commit`). Who confirmed is the draft's owner, never
      something the client sends; the accepted warnings are the server's own
      list for the validation the person saw, named by its digest. A draft
      commits once: asked again, it answers with the run it has.
    * **The history and the detail** of every load, the web app's and the
      command line's, and the original file a load came from.

    Nothing here writes to Silver or Gold. Bronze is written by the committer
    only, in its one transaction.

Depends on:
    ``energy.domain``, ``energy.ports``, ``energy.services.draft_service``,
    ``energy.core``.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from energy.core.logging import get_logger
from energy.domain.dataset_spec import DatasetKind, spec_digest
from energy.domain.draft_commit import (
    CommitBlock,
    DifferenceCounts,
    blocked_by,
    flow_name,
    run_parameters,
    validation_digest,
    warnings_of,
)
from energy.domain.drafts import XLSX_MEDIA_TYPE, DraftStatus
from energy.domain.exceptions import (
    DraftBlockedError,
    EntityNotFoundError,
    NothingToLoadError,
    NotValidatedError,
    OriginalMissingError,
    UnknownDraftError,
    UnknownLoadError,
    ValidationChangedError,
    WarningsNotAcceptedError,
)
from energy.domain.landing import landing_source
from energy.domain.qad_export import export_info
from energy.ports.bronze_landing import NewRawFile, NewRun
from energy.ports.load_committer import CommitOrder, DatasetTarget

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date
    from io import BufferedReader

    from energy.domain.dataset_spec import DatasetCatalogue, DatasetSpec
    from energy.domain.draft_commit import RowChange
    from energy.domain.draft_rules import RuleCount
    from energy.domain.drafts import Draft
    from energy.ports.load_committer import DifferencePage, LoadCommitter, LoadReference
    from energy.ports.load_ledger import (
        ArchivedOriginal,
        LoadFilter,
        LoadLedger,
        LoadPage,
        LoadRecord,
        LoadSort,
        LoadSummary,
    )
    from energy.ports.object_storage import ObjectStorage
    from energy.services.draft_service import DraftService

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CommitRequest:
    """What a person confirms.

    Attributes:
        validation_digest: the digest of the validation they saw.
        accept_warnings: whether they accepted its warnings.
        base_run_id: for a catalogue, the current version they were shown;
            ``None`` when there was none. Ignored for movements.
    """

    validation_digest: str
    accept_warnings: bool = False
    base_run_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Confirmation:
    """What committing a draft would write.

    Attributes:
        draft: the draft, validated again if it was stale.
        spec: its dataset spec.
        blocked_by: why it cannot be committed now; ``None`` when it can.
        validation_digest: the digest a commit must carry; ``None`` before a
            validation.
        rows: the rows that would land.
        warnings: the warnings to accept, by rule and column.
        period: for a movement, the first and the last date its rows cover.
        overlapping: for a movement, the loads whose period meets it.
        current_version: for a catalogue, the load that is current now.
        difference: for a catalogue, how the draft differs from it.
        already_loaded: the run that loaded the same file, if one did.
    """

    draft: Draft
    spec: DatasetSpec
    blocked_by: CommitBlock | None
    validation_digest: str | None
    rows: int
    warnings: tuple[RuleCount, ...]
    period: tuple[date, date] | None
    overlapping: tuple[LoadReference, ...]
    current_version: LoadReference | None
    difference: DifferenceCounts | None
    already_loaded: UUID | None


@dataclass(frozen=True, slots=True)
class CommitOutcome:
    """A committed load.

    Attributes:
        load: the load, as the detail page shows it.
        created: ``False`` when the draft had been committed before.
    """

    load: LoadRecord
    created: bool


@dataclass(frozen=True, slots=True)
class LoadView:
    """One load, for a viewer.

    Attributes:
        record: the load.
        draft_available: whether the viewer can still open the draft it came
            from: it is alive, and it is theirs.
    """

    record: LoadRecord
    draft_available: bool


def target_of(spec: DatasetSpec) -> DatasetTarget:
    """Return where a dataset's rows land in Bronze."""
    return DatasetTarget(spec.bronze.source_key, spec.bronze.sheet, spec.bronze.dataset_key)


class LoadService:
    """Confirms drafts into Bronze, and reads Bronze's record of loads.

    Args:
        drafts: the draft service, which finds and validates drafts.
        committer: lands drafts in Bronze.
        ledger: reads the record of loads.
        storage: where originals are archived.
        catalogue: the dataset specs.
    """

    def __init__(
        self,
        drafts: DraftService,
        committer: LoadCommitter,
        ledger: LoadLedger,
        storage: ObjectStorage,
        catalogue: DatasetCatalogue,
    ) -> None:
        self._drafts = drafts
        self._committer = committer
        self._ledger = ledger
        self._storage = storage
        self._catalogue = catalogue

    # -------------------------------------------------------------------------
    # The confirmation
    # -------------------------------------------------------------------------
    def confirmation(self, draft_id: UUID, owner: str) -> Confirmation:
        """Return what committing one of the owner's drafts would write.

        Raises:
            UnknownDraftError: if ``owner`` has no live draft of that ID.
            DraftBusyError: if a stale draft must be validated again and
                another validation of it is running.
        """
        draft = self._drafts.get(draft_id, owner)
        spec = self._catalogue.get(draft.dataset_key)
        draft = self._fresh(draft, spec, owner)
        summary = draft.validation
        target = target_of(spec)
        already = (
            self._committer.landed_by(draft.content_hash, target)
            if draft.content_hash and draft.status is DraftStatus.VALIDATED
            else None
        )
        facts = summary.facts if summary else None
        period = None
        overlapping: tuple[LoadReference, ...] = ()
        if spec.kind is DatasetKind.MOVEMENT and facts and facts.window_start and facts.window_end:
            period = (facts.window_start, facts.window_end)
            overlapping = self._committer.overlapping(target, draft.plant_code, *period)
        current = None
        difference = None
        if spec.kind is DatasetKind.SNAPSHOT and draft.status is DraftStatus.VALIDATED:
            current = self._committer.current_version(target, draft.plant_code)
            difference = self._committer.difference(
                draft.draft_id,
                current.run_id if current else None,
                target,
                spec.natural_key,
            )
        return Confirmation(
            draft=draft,
            spec=spec,
            blocked_by=blocked_by(draft, already_loaded=already is not None),
            validation_digest=validation_digest(summary) if summary else None,
            rows=summary.rows_read if summary else 0,
            warnings=warnings_of(summary) if summary else (),
            period=period,
            overlapping=overlapping,
            current_version=current,
            difference=difference,
            already_loaded=already,
        )

    def difference(
        self,
        draft_id: UUID,
        owner: str,
        change: RowChange,
        offset: int,
        limit: int,
    ) -> DifferencePage:
        """Return a page of the rows a catalogue's new version adds, changes or removes.

        Raises:
            UnknownDraftError: if ``owner`` has no live draft of that ID, or it
                is not a validated draft of a catalogue.
        """
        draft = self._drafts.get(draft_id, owner)
        spec = self._catalogue.get(draft.dataset_key)
        if spec.kind is not DatasetKind.SNAPSHOT or draft.status is not DraftStatus.VALIDATED:
            raise UnknownDraftError(
                "this draft is no validated catalogue to compare", draft_id=str(draft_id)
            )
        target = target_of(spec)
        current = self._committer.current_version(target, draft.plant_code)
        return self._committer.difference_page(
            draft_id,
            current.run_id if current else None,
            target,
            spec.natural_key,
            change,
            offset,
            limit,
        )

    def _fresh(self, draft: Draft, spec: DatasetSpec, owner: str) -> Draft:
        """Return the draft, validated again first if its validation is stale."""
        summary = draft.validation
        if draft.status is not DraftStatus.VALIDATED or summary is None:
            return draft
        if summary.facts is not None and summary.spec_digest == spec_digest(spec):
            return draft
        logger.info("stale draft validated again before its commit", draft_id=str(draft.draft_id))
        return self._drafts.validate(draft.draft_id, owner)

    # -------------------------------------------------------------------------
    # The commit
    # -------------------------------------------------------------------------
    def commit(self, draft_id: UUID, owner: str, request: CommitRequest) -> CommitOutcome:
        """Land one of the owner's drafts in Bronze, once.

        Raises:
            UnknownDraftError: no live draft of ``owner`` has that ID (404).
            DraftBusyError: a validation or a commit holds it (409).
            NotValidatedError: it has not been validated (409).
            ValidationChangedError: its validation is not the one confirmed (409).
            CurrentVersionChangedError: another load became current (409).
            AlreadyLoadedError: a completed run loaded the same file (409).
            DatasetBusyError: another commit of the dataset held its lock (409).
            OriginalMissingError: its archived original is gone (409).
            DraftBlockedError: it has errors (422).
            WarningsNotAcceptedError: its warnings were not accepted (422).
            NothingToLoadError: it has no row (422).
        """
        draft = self._drafts.get(draft_id, owner)
        if draft.status is DraftStatus.COMMITTED and draft.run_id is not None:
            return CommitOutcome(self._committed(draft.run_id, draft.plant_code), created=False)
        spec = self._catalogue.get(draft.dataset_key)
        draft = self._fresh(draft, spec, owner)
        _refuse(draft, request)
        self._check_original(draft)
        snapshot = spec.kind is DatasetKind.SNAPSHOT
        base_run_id = request.base_run_id if snapshot else None
        target = target_of(spec)
        difference = (
            self._committer.difference(draft_id, base_run_id, target, spec.natural_key)
            if snapshot
            else None
        )
        order = CommitOrder(
            draft_id=draft_id,
            owner=owner,
            target=target,
            snapshot=snapshot,
            base_run_id=base_run_id,
            file=_raw_file(draft, spec),
            run=_run(draft, spec, request.accept_warnings, difference),
            rows=draft.validation.rows_read if draft.validation else 0,
        )
        committed = self._committer.commit(order, lambda locked: _refuse(locked, request))
        return CommitOutcome(
            self._committed(committed.run_id, draft.plant_code), created=committed.created
        )

    def _committed(self, run_id: UUID, plant_code: str) -> LoadRecord:
        """Return a committed load as the detail page shows it."""
        record = self._ledger.load(run_id, (plant_code,))
        if record is None:  # pragma: no cover - the commit has just written it
            raise UnknownLoadError("the committed load cannot be read", run_id=str(run_id))
        return record

    def _check_original(self, draft: Draft) -> None:
        """Refuse a draft whose archived original is gone: Bronze would point at nothing."""
        if draft.bucket is None or draft.object_key is None:
            raise OriginalMissingError(
                "the draft names no archived file", draft_id=str(draft.draft_id)
            )
        try:
            self._storage.stat(draft.bucket, draft.object_key)
        except EntityNotFoundError as exc:
            raise OriginalMissingError(
                "the draft's archived file is gone", draft_id=str(draft.draft_id)
            ) from exc

    # -------------------------------------------------------------------------
    # History
    # -------------------------------------------------------------------------
    def loads(
        self, wanted: LoadFilter, sort: LoadSort, descending: bool, offset: int, limit: int
    ) -> LoadPage:
        """Return a page of the loads the caller may see, each named by its dataset."""
        page = self._ledger.loads(wanted, sort, descending, offset, limit)
        return dataclasses.replace(page, items=tuple(self._named(item) for item in page.items))

    def _named(self, summary: LoadSummary) -> LoadSummary:
        """Name a command-line load by the dataset whose sheet it landed, when one spec has it.

        A load from the web app names its dataset itself. One from the command
        line names only its sheets; when it landed a single sheet a spec
        describes, the spec names it, so the history needs no Bronze name.
        """
        if summary.dataset_key is not None or len(summary.sheets) != 1:
            return summary
        (sheet,) = summary.sheets
        landed = DatasetTarget(summary.source_key, sheet.name, sheet.dataset_key)
        for spec in self._catalogue:
            if target_of(spec) == landed:
                return dataclasses.replace(summary, dataset_key=spec.key)
        return summary

    def load(self, run_id: UUID, plant_scope: Sequence[str], viewer: str) -> LoadView:
        """Return one load, and whether the viewer can still open its draft.

        Raises:
            UnknownLoadError: if no load of the caller's plants has that ID.
        """
        record = self._ledger.load(run_id, plant_scope)
        if record is None:
            raise UnknownLoadError("no load of your plants has that ID", run_id=str(run_id))
        named = dataclasses.replace(record, summary=self._named(record.summary))
        return LoadView(named, draft_available=self._draft_available(record, viewer))

    def _draft_available(self, record: LoadRecord, viewer: str) -> bool:
        """Return whether the viewer owns the live draft a load came from."""
        draft_id = record.parameters.get("draft_id")
        if not isinstance(draft_id, str):
            return False
        try:
            self._drafts.get(UUID(draft_id), viewer)
        except (UnknownDraftError, ValueError):
            return False
        return True

    def original(
        self, run_id: UUID, plant_scope: Sequence[str]
    ) -> tuple[ArchivedOriginal, BufferedReader]:
        """Open the file a load came from, for streaming.

        Raises:
            UnknownLoadError: if no load of the caller's plants has that ID, or
                Bronze catalogues no file for it.
            EntityNotFoundError: if the archive no longer holds the file.
        """
        record = self._ledger.load(run_id, plant_scope)
        if record is None or record.original is None:
            raise UnknownLoadError("no original file for that load", run_id=str(run_id))
        original = record.original
        return original, self._storage.open_stream(original.bucket, original.object_key)

    def last_loads(self, plant_scope: Sequence[str]) -> Mapping[str, LoadReference]:
        """Return the latest completed load of each dataset, by dataset key."""
        targets = {spec.key: target_of(spec) for spec in self._catalogue}
        found = self._ledger.last_loads(tuple(targets.values()), plant_scope)
        return {key: found[target] for key, target in targets.items() if target in found}


def _refuse(draft: Draft, request: CommitRequest) -> None:
    """Refuse a draft that cannot be committed as the person confirmed it."""
    block = blocked_by(draft)
    if block is CommitBlock.COMMITTED:
        return
    if block is CommitBlock.NOT_VALIDATED or draft.validation is None:
        raise NotValidatedError("the draft has not been validated", draft_id=str(draft.draft_id))
    if block is CommitBlock.ERRORS:
        raise DraftBlockedError("the draft has errors", draft_id=str(draft.draft_id))
    if block is CommitBlock.NOTHING_TO_LOAD:
        raise NothingToLoadError("the draft has no row to load", draft_id=str(draft.draft_id))
    if validation_digest(draft.validation) != request.validation_digest:
        raise ValidationChangedError(
            "the draft's validation is not the one confirmed", draft_id=str(draft.draft_id)
        )
    if warnings_of(draft.validation) and not request.accept_warnings:
        raise WarningsNotAcceptedError(
            "the draft's warnings were not accepted", draft_id=str(draft.draft_id)
        )


def _raw_file(draft: Draft, spec: DatasetSpec) -> NewRawFile:
    """Return the original as Bronze catalogues it."""
    facts = draft.validation.facts if draft.validation else None
    return NewRawFile(
        content_hash=draft.content_hash or "",
        bucket=draft.bucket or "",
        object_key=draft.object_key or "",
        filename=draft.filename,
        content_type=XLSX_MEDIA_TYPE,
        size_bytes=draft.size_bytes or 0,
        plant_code=draft.plant_code,
        source_key=spec.bronze.source_key,
        sheet_names=facts.sheet_names if facts else (spec.bronze.sheet,),
        uploaded_by=draft.created_by,
    )


def _run(
    draft: Draft, spec: DatasetSpec, accept_warnings: bool, difference: DifferenceCounts | None
) -> NewRun:
    """Return the run a commit opens, as the command-line loader would describe it."""
    facts = draft.validation.facts if draft.validation else None
    info = facts.export_info if facts else {}
    export = export_info(info)
    return NewRun(
        flow_name=flow_name(draft.filename),
        source_key=spec.bronze.source_key,
        source_system=landing_source(spec.bronze.source_key).source_system,
        plant_code=draft.plant_code,
        window_start=facts.window_start if facts else None,
        window_end=facts.window_end if facts else None,
        qad_program=export.program,
        qad_exploration=export.exploration,
        qad_user=export.user,
        queried_at=export.queried_at,
        search_conditions=facts.search_conditions if facts else (),
        parameters=run_parameters(draft, spec, accept_warnings, difference),
    )


__all__ = [
    "CommitOutcome",
    "CommitRequest",
    "Confirmation",
    "LoadService",
    "LoadView",
    "target_of",
]
