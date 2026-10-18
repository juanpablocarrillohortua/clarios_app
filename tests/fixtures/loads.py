"""In-memory stand-ins for the load committer and the load ledger, for tests without a database.

They share one :class:`InMemoryBronze`: the runs, the catalogued files and
the landed rows a commit writes and a history reads. They keep the promises
the tests rely on, which ``tests/integration/test_commit_end_to_end.py``
checks the PostgreSQL adapters against:

- a commit locks its draft (a second one at the same time is refused), checks
  it, refuses a catalogue whose current version moved, refuses a file whose
  sheet a completed run already landed, lands every staged row, checks the
  count, removes the staged rows and marks the draft committed, all or
  nothing;
- a committed draft commits once: asked again it returns its run;
- a catalogue's current version is its latest completed load;
- a history lists the loads of the caller's plants only.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from energy.domain.draft_commit import DifferenceCounts, LoadOrigin, RowChange
from energy.domain.drafts import DRAFT_RETENTION, DraftStatus
from energy.domain.exceptions import (
    AlreadyLoadedError,
    CommitCountMismatchError,
    CurrentVersionChangedError,
    DatasetBusyError,
    DraftBusyError,
    UnknownDraftError,
)
from energy.domain.landing import canonical_json, sha256_hex
from energy.ports.load_committer import (
    ColumnChange,
    CommittedLoad,
    DifferencePage,
    DifferenceRow,
    LoadReference,
)
from energy.ports.load_ledger import (
    ArchivedOriginal,
    LoadPage,
    LoadRecord,
    LoadSort,
    LoadSummary,
    Person,
    SheetLoaded,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from tests.fixtures.drafts import InMemoryDraftRepository

    from energy.domain.dataset_spec import DatasetCatalogue
    from energy.domain.drafts import Draft
    from energy.ports.load_committer import CommitOrder, DatasetTarget
    from energy.ports.load_ledger import LoadFilter
    from energy.ports.object_storage import ObjectStorage
    from energy.services.draft_service import DraftService
    from energy.services.load_service import LoadService


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class LandedRow:
    """One row in a landing table."""

    sheet_name: str
    dataset_key: str | None
    source_row: int
    content_hash: str
    payload: dict[str, str]
    raw_file_id: UUID


@dataclass
class StoredRun:
    """One run of the ledger, with the rows it landed."""

    run_id: UUID
    source_key: str
    plant_code: str
    flow_name: str
    state: str
    started_at: datetime
    finished_at: datetime | None
    window_start: date | None
    window_end: date | None
    parameters: dict[str, object]
    rows: list[LandedRow] = field(default_factory=list)
    error: str = ""
    qad: dict[str, object] = field(default_factory=dict)

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass
class StoredFile:
    """One file of the catalogue."""

    raw_file_id: UUID
    content_hash: str
    bucket: str
    object_key: str
    filename: str
    content_type: str
    size_bytes: int
    plant_code: str
    source_key: str
    sheet_names: tuple[str, ...]
    uploaded_by: str


class InMemoryBronze:
    """Bronze, in dictionaries: the run ledger, the file catalogue, the landed rows."""

    def __init__(self) -> None:
        self.runs: dict[UUID, StoredRun] = {}
        self.files: dict[str, StoredFile] = {}
        self._last: datetime | None = None

    def stamp(self) -> datetime:
        """Return the time now, always later than the last one given.

        The Windows clock ticks about every 15 ms, so two runs recorded one
        after the other could otherwise share a time and be ordered at random.
        """
        now = _now()
        if self._last is not None and now <= self._last:
            now = self._last + timedelta(microseconds=1)
        self._last = now
        return now

    def add_cli_load(
        self,
        target: DatasetTarget,
        payloads: Sequence[Mapping[str, str]],
        *,
        content_hash: str = "c" * 64,
        filename: str = "EQUS BATT.xlsx",
        plant_code: str = "1515",
        state: str = "completed",
        window: tuple[date, date] | None = None,
    ) -> UUID:
        """Plant a load as the command line would record it, and return its run."""
        known = self.files.get(content_hash)
        if known is None:
            known = StoredFile(
                uuid4(),
                content_hash,
                "bronze-raw",
                f"cli/{content_hash}.xlsx",
                filename,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                1000,
                plant_code,
                target.source_key,
                (target.sheet_name,),
                "system",
            )
            self.files[content_hash] = known
        run = StoredRun(
            run_id=uuid4(),
            source_key=target.source_key,
            plant_code=plant_code,
            flow_name=filename.rsplit(".", 1)[0],
            state=state,
            started_at=(moment := self.stamp()),
            finished_at=moment,
            window_start=window[0] if window else None,
            window_end=window[1] if window else None,
            parameters={
                "filename": filename,
                "content_hash": content_hash,
                "sheets": [
                    {"name": target.sheet_name, "dataset_key": target.dataset_key, "header_row": 1}
                ],
                "uploaded_by": "system",
            },
        )
        for number, payload in enumerate(payloads, start=2):
            text = canonical_json(dict(payload))
            run.rows.append(
                LandedRow(
                    target.sheet_name,
                    target.dataset_key,
                    number,
                    sha256_hex(text.encode("utf-8")),
                    dict(payload),
                    known.raw_file_id,
                )
            )
        self.runs[run.run_id] = run
        return run.run_id

    def runs_of(self, target: DatasetTarget) -> list[StoredRun]:
        """Return the runs that landed rows of a dataset, newest first."""
        return sorted(
            (
                run
                for run in self.runs.values()
                if run.source_key == target.source_key
                and any(_is_of(row, target) for row in run.rows)
            ),
            key=lambda run: (run.finished_at or run.started_at, str(run.run_id)),
            reverse=True,
        )


def _is_of(row: LandedRow, target: DatasetTarget) -> bool:
    """Return whether a landed row is of a dataset, by the rule Silver reads it by."""
    if target.dataset_key is not None:
        return row.dataset_key == target.dataset_key
    return row.sheet_name == target.sheet_name


def _reference(run: StoredRun, target: DatasetTarget) -> LoadReference:
    return LoadReference(
        run_id=run.run_id,
        origin=LoadOrigin.APP if "input_mode" in run.parameters else LoadOrigin.CLI,
        finished_at=run.finished_at,
        filename=_text(run.parameters.get("filename")),
        rows=sum(1 for row in run.rows if _is_of(row, target)),
        uploaded_by_name=_text(run.parameters.get("uploaded_by_name")),
        window_start=run.window_start,
        window_end=run.window_end,
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _key(payload: Mapping[str, str], natural_key: Sequence[str]) -> tuple[str, ...]:
    return tuple(payload.get(name, "").strip() for name in natural_key)


def _keyed(
    rows: Sequence[tuple[int, str, dict[str, str]]], natural_key: Sequence[str]
) -> dict[tuple[tuple[str, ...], int], tuple[int, str, dict[str, str]]]:
    """Key rows by natural key and occurrence, in sheet order."""
    seen: dict[tuple[str, ...], int] = {}
    keyed = {}
    for row in sorted(rows, key=lambda item: item[0]):
        key = _key(row[2], natural_key)
        seen[key] = seen.get(key, 0) + 1
        keyed[(key, seen[key])] = row
    return keyed


class InMemoryLoadCommitter:
    """Lands drafts of an in-memory draft repository in an in-memory Bronze."""

    def __init__(self, drafts: InMemoryDraftRepository, bronze: InMemoryBronze) -> None:
        self._drafts = drafts
        self.bronze = bronze
        self.held_drafts: set[UUID] = set()
        self.held_datasets: set[DatasetTarget] = set()
        #: Called after the rows land and before the run completes: a test may
        #: raise from it to prove that a failure leaves everything as it was.
        self.after_rows: Callable[[], None] | None = None

    def current_version(self, target: DatasetTarget, plant_code: str) -> LoadReference | None:  # noqa: ARG002
        completed = [run for run in self.bronze.runs_of(target) if run.state == "completed"]
        return _reference(completed[0], target) if completed else None

    def landed_by(self, content_hash: str, target: DatasetTarget) -> UUID | None:
        known = self.bronze.files.get(content_hash)
        if known is None:
            return None
        for run in self.bronze.runs_of(target):
            if run.state == "completed" and any(
                row.raw_file_id == known.raw_file_id and _is_of(row, target) for row in run.rows
            ):
                return run.run_id
        return None

    def overlapping(
        self, target: DatasetTarget, plant_code: str, start: date, end: date
    ) -> tuple[LoadReference, ...]:
        return tuple(
            _reference(run, target)
            for run in self.bronze.runs_of(target)
            if run.state == "completed"
            and run.plant_code == plant_code
            and run.window_start is not None
            and run.window_end is not None
            and run.window_start <= end
            and run.window_end >= start
        )

    def _pairs(
        self,
        draft_id: UUID,
        base_run_id: UUID | None,
        target: DatasetTarget,
        natural_key: Sequence[str],
    ) -> list[DifferenceRow | None]:
        fresh = _keyed(
            [
                (row.source_row, row.content_hash, json.loads(row.payload_json))
                for row in self._drafts.rows.get(draft_id, [])
            ],
            natural_key,
        )
        base = self.bronze.runs.get(base_run_id) if base_run_id else None
        current = _keyed(
            [
                (row.source_row, row.content_hash, row.payload)
                for row in (base.rows if base else [])
                if _is_of(row, target)
            ],
            natural_key,
        )
        pairs: list[DifferenceRow | None] = []
        for paired in sorted(set(fresh) | set(current)):
            new, old = fresh.get(paired), current.get(paired)
            key = tuple((new or old)[2].get(name) for name in natural_key)  # type: ignore[index]
            if old is None and new is not None:
                pairs.append(DifferenceRow(RowChange.ADDED, key, new[0], None, new[2]))
            elif new is None and old is not None:
                pairs.append(DifferenceRow(RowChange.REMOVED, key, None, old[0], old[2]))
            elif new is not None and old is not None and new[1] != old[1]:
                names = list(dict.fromkeys([*new[2], *old[2]]))
                columns = tuple(
                    ColumnChange(name, old[2].get(name), new[2].get(name))
                    for name in names
                    if old[2].get(name) != new[2].get(name)
                )
                pairs.append(DifferenceRow(RowChange.CHANGED, key, new[0], old[0], {}, columns))
            else:
                pairs.append(None)
        return pairs

    def difference(
        self,
        draft_id: UUID,
        base_run_id: UUID | None,
        target: DatasetTarget,
        natural_key: Sequence[str],
    ) -> DifferenceCounts:
        pairs = self._pairs(draft_id, base_run_id, target, natural_key)
        changes = [pair.change if pair else None for pair in pairs]
        return DifferenceCounts(
            base_run_id=base_run_id,
            added=changes.count(RowChange.ADDED),
            changed=changes.count(RowChange.CHANGED),
            removed=changes.count(RowChange.REMOVED),
            unchanged=changes.count(None),
        )

    def difference_page(
        self,
        draft_id: UUID,
        base_run_id: UUID | None,
        target: DatasetTarget,
        natural_key: Sequence[str],
        change: RowChange,
        offset: int,
        limit: int,
    ) -> DifferencePage:
        rows = [
            pair
            for pair in self._pairs(draft_id, base_run_id, target, natural_key)
            if pair is not None and pair.change is change
        ]
        return DifferencePage(total=len(rows), items=tuple(rows[offset : offset + limit]))

    def commit(self, order: CommitOrder, check: Callable[[Draft], None]) -> CommittedLoad:
        if order.draft_id in self.held_drafts:
            raise DraftBusyError("the draft is being validated or committed")
        draft = self._drafts.find(order.draft_id, order.owner)
        if draft is None:
            raise UnknownDraftError("no draft of yours has that ID")
        if draft.status is DraftStatus.COMMITTED and draft.run_id is not None:
            rows = draft.validation.rows_read if draft.validation else 0
            return CommittedLoad(draft.run_id, rows, created=False)
        check(draft)
        if order.target in self.held_datasets:
            raise DatasetBusyError("another commit of this dataset is running")
        if order.snapshot:
            current = self.current_version(order.target, order.run.plant_code)
            if (current.run_id if current else None) != order.base_run_id:
                raise CurrentVersionChangedError("another load became the current version")
        file = order.file
        known = self.bronze.files.get(file.content_hash)
        if known is not None:
            landed = self.landed_by(file.content_hash, order.target)
            if landed is not None:
                raise AlreadyLoadedError("already loaded", run_id=str(landed))
        else:
            known = StoredFile(
                uuid4(),
                file.content_hash,
                file.bucket,
                file.object_key,
                file.filename,
                file.content_type,
                file.size_bytes,
                file.plant_code,
                file.source_key,
                tuple(file.sheet_names),
                file.uploaded_by,
            )
        run = StoredRun(
            run_id=uuid4(),
            source_key=order.run.source_key,
            plant_code=order.run.plant_code,
            flow_name=order.run.flow_name,
            state="completed",
            started_at=(moment := self.bronze.stamp()),
            finished_at=moment,
            window_start=order.run.window_start,
            window_end=order.run.window_end,
            parameters=json.loads(json.dumps(dict(order.run.parameters), default=str)),
            qad={
                "qad_program": order.run.qad_program,
                "qad_exploration": order.run.qad_exploration,
                "qad_user": order.run.qad_user,
                "queried_at": order.run.queried_at,
                "search_conditions": list(order.run.search_conditions),
            },
        )
        run.rows = [
            LandedRow(
                order.target.sheet_name,
                order.target.dataset_key,
                row.source_row,
                row.content_hash,
                json.loads(row.payload_json),
                known.raw_file_id,
            )
            for row in sorted(self._drafts.rows.get(order.draft_id, []), key=lambda r: r.source_row)
        ]
        if self.after_rows is not None:
            self.after_rows()  # nothing is kept when it raises: all or nothing
        if run.row_count != order.rows:
            raise CommitCountMismatchError("the rows landed are not the rows staged")
        # Everything held: write it all at once.
        self.bronze.files[known.content_hash] = known
        self.bronze.runs[run.run_id] = run
        self._drafts.rows[order.draft_id] = []
        self._drafts.drafts[order.draft_id] = dataclasses.replace(
            draft,
            status=DraftStatus.COMMITTED,
            run_id=run.run_id,
            committed_at=_now(),
            expires_at=_now() + DRAFT_RETENTION,
        )
        return CommittedLoad(run.run_id, run.row_count, created=True)


class InMemoryLoadLedger:
    """Reads an in-memory Bronze as a history of loads."""

    def __init__(self, bronze: InMemoryBronze) -> None:
        self.bronze = bronze

    def _summary(self, run: StoredRun, counted: bool = False) -> LoadSummary:
        parameters = run.parameters
        sheets_named = (
            parameters.get("sheets") if isinstance(parameters.get("sheets"), list) else []
        )
        sheets = tuple(
            SheetLoaded(
                str(item["name"]),
                item.get("dataset_key"),
                sum(1 for row in run.rows if row.sheet_name == item["name"]) if counted else None,
            )
            for item in sheets_named  # type: ignore[union-attr]
        )
        accepted = (parameters.get("validation") or {}).get("accepted")  # type: ignore[union-attr]
        return LoadSummary(
            run_id=run.run_id,
            origin=LoadOrigin.APP if "input_mode" in parameters else LoadOrigin.CLI,
            state=run.state,
            source_key=run.source_key,
            dataset_key=_text(parameters.get("dataset_key")),
            sheets=sheets,
            filename=_text(parameters.get("filename")),
            rows=run.row_count,
            started_at=run.started_at,
            finished_at=run.finished_at,
            window_start=run.window_start,
            window_end=run.window_end,
            uploaded_by=_text(parameters.get("uploaded_by")) or "system",
            uploaded_by_name=_text(parameters.get("uploaded_by_name")),
            warnings_accepted=sum(item["rows"] for item in accepted["warnings"]) if accepted else 0,
            error=run.error,
        )

    def _matches(self, run: StoredRun, wanted: LoadFilter) -> bool:
        summary = self._summary(run)
        target = wanted.target
        return (
            run.plant_code in wanted.plant_scope
            and (
                target is None
                or (
                    run.source_key == target.source_key
                    and any(
                        sheet.name == target.sheet_name
                        and (target.dataset_key is None or sheet.dataset_key == target.dataset_key)
                        for sheet in summary.sheets
                    )
                )
            )
            and (wanted.state is None or run.state == wanted.state)
            and (wanted.uploaded_by is None or summary.uploaded_by == wanted.uploaded_by)
            and (wanted.origin is None or summary.origin is wanted.origin)
            and (
                wanted.period_from is None
                or (run.window_end is not None and run.window_end >= wanted.period_from)
            )
            and (
                wanted.period_to is None
                or (run.window_start is not None and run.window_start <= wanted.period_to)
            )
            and (wanted.started_from is None or run.started_at.date() >= wanted.started_from)
            and (wanted.started_to is None or run.started_at.date() <= wanted.started_to)
        )

    def loads(
        self, wanted: LoadFilter, sort: LoadSort, descending: bool, offset: int, limit: int
    ) -> LoadPage:
        matching = [run for run in self.bronze.runs.values() if self._matches(run, wanted)]
        matching.sort(
            key=lambda run: (
                run.started_at if sort is LoadSort.STARTED_AT else run.row_count,
                run.started_at,
                str(run.run_id),
            ),
            reverse=descending,
        )
        people = {
            str(run.parameters["uploaded_by"]): _text(run.parameters.get("uploaded_by_name"))
            for run in self.bronze.runs.values()
            if "input_mode" in run.parameters and run.plant_code in wanted.plant_scope
        }
        return LoadPage(
            total=len(matching),
            items=tuple(self._summary(run) for run in matching[offset : offset + limit]),
            people=tuple(
                Person(subject, name)
                for subject, name in sorted(
                    people.items(), key=lambda item: (item[1] or "", item[0])
                )
            ),
        )

    def load(self, run_id: UUID, plant_scope: Sequence[str]) -> LoadRecord | None:
        run = self.bronze.runs.get(run_id)
        if run is None or run.plant_code not in plant_scope:
            return None
        known = self.bronze.files.get(str(run.parameters.get("content_hash")))
        return LoadRecord(
            summary=self._summary(run, counted=True),
            plant_code=run.plant_code,
            flow_name=run.flow_name,
            original=ArchivedOriginal(
                known.bucket,
                known.object_key,
                known.filename,
                known.content_type,
                known.size_bytes,
                known.content_hash,
            )
            if known
            else None,
            parameters=run.parameters,
        )

    def last_loads(
        self, targets: Sequence[DatasetTarget], plant_scope: Sequence[str]
    ) -> Mapping[DatasetTarget, LoadReference]:
        found = {}
        for target in targets:
            completed = [
                run
                for run in self.bronze.runs_of(target)
                if run.state == "completed" and run.plant_code in plant_scope
            ]
            if completed:
                found[target] = _reference(completed[0], target)
        return found


def build_load_service(
    drafts: DraftService,
    repository: InMemoryDraftRepository,
    storage: ObjectStorage,
    catalogue: DatasetCatalogue,
    bronze: InMemoryBronze | None = None,
) -> tuple[LoadService, InMemoryLoadCommitter, InMemoryLoadLedger]:
    """Return the real load service on the in-memory committer and ledger, and both."""
    from energy.services.load_service import LoadService  # noqa: PLC0415

    shared = bronze if bronze is not None else InMemoryBronze()
    committer = InMemoryLoadCommitter(repository, shared)
    ledger = InMemoryLoadLedger(shared)
    service = LoadService(
        drafts=drafts, committer=committer, ledger=ledger, storage=storage, catalogue=catalogue
    )
    return service, committer, ledger


__all__ = [
    "InMemoryBronze",
    "InMemoryLoadCommitter",
    "InMemoryLoadLedger",
    "LandedRow",
    "StoredFile",
    "StoredRun",
    "build_load_service",
]
