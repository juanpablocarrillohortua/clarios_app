"""Port for committing a draft into Bronze, and for what the confirmation reads first.

Layer: ports

Responsibility:
    Declares how a validated draft lands in Bronze (increment 7), and what a
    person is shown before confirming it: the load that is current for a
    catalogue and how the new version differs from it, the loads that cover the
    same period, and whether the same file was landed already.

    :meth:`LoadCommitter.commit` is one transaction. It locks the draft, takes a
    lock of its own per dataset, catalogues the original if Bronze does not know
    it yet, opens a run, lands every staged row, completes the run, removes the
    staged rows and marks the draft committed. Any failure rolls all of it back:
    Bronze is then exactly as it was and the draft is still validated. A draft
    commits once; asked again, the commit returns the run it already has.

Depends on:
    ``energy.domain``, ``energy.ports.bronze_landing``, standard library
    typing only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import date, datetime
    from uuid import UUID

    from energy.domain.draft_commit import DifferenceCounts, LoadOrigin, RowChange
    from energy.domain.drafts import Draft
    from energy.ports.bronze_landing import NewRawFile, NewRun


@dataclass(frozen=True, slots=True)
class DatasetTarget:
    """Where a dataset's rows land in Bronze, which is how its loads are found.

    Attributes:
        source_key: the Bronze source.
        sheet_name: the worksheet the rows come from.
        dataset_key: the catalogue's name, for the sources that need one.
    """

    source_key: str
    sheet_name: str
    dataset_key: str | None


@dataclass(frozen=True, slots=True)
class LoadReference:
    """A completed load, as a confirmation names it.

    Attributes:
        run_id: the run.
        origin: the web app or the command line.
        finished_at: when it completed.
        filename: the file it came from.
        rows: the rows it landed.
        uploaded_by_name: who loaded it, for display; ``None`` from the command line.
        window_start: the first date its rows cover, for movements.
        window_end: the last date, likewise.
    """

    run_id: UUID
    origin: LoadOrigin
    finished_at: datetime | None
    filename: str | None
    rows: int
    uploaded_by_name: str | None
    window_start: date | None = None
    window_end: date | None = None


@dataclass(frozen=True, slots=True)
class ColumnChange:
    """One column of a changed row: what it held, and what it holds now."""

    key: str
    before: str | None
    after: str | None


@dataclass(frozen=True, slots=True)
class DifferenceRow:
    """One row of a catalogue's new version that differs from the current one.

    Attributes:
        change: added, changed or removed.
        key: the row's natural key values, in the spec's order.
        source_row: its row in the new file; ``None`` for a removed row.
        previous_source_row: its row in the current version; ``None`` for an
            added row.
        values: an added row's values, or a removed row's; empty for a changed row.
        columns: a changed row's columns that differ; empty otherwise.
    """

    change: RowChange
    key: tuple[str | None, ...]
    source_row: int | None
    previous_source_row: int | None
    values: Mapping[str, str]
    columns: tuple[ColumnChange, ...] = ()


@dataclass(frozen=True, slots=True)
class DifferencePage:
    """A page of the rows that differ, of one kind of change."""

    total: int
    items: tuple[DifferenceRow, ...]


@dataclass(frozen=True, slots=True)
class CommitOrder:
    """Everything one commit writes, decided before its transaction opens.

    Attributes:
        draft_id: the draft.
        owner: its owner, the only person who may commit it.
        target: where its rows land.
        snapshot: whether the dataset is a snapshot, whose current version
            must still be ``base_run_id`` inside the transaction.
        base_run_id: the current version the person confirmed against.
        file: the original, as Bronze catalogues it if it does not know it.
        run: the run to open, with its parameters.
        rows: how many rows the draft staged, which is how many must land.
    """

    draft_id: UUID
    owner: str
    target: DatasetTarget
    snapshot: bool
    base_run_id: UUID | None
    file: NewRawFile
    run: NewRun
    rows: int


@dataclass(frozen=True, slots=True)
class CommittedLoad:
    """The outcome of a commit.

    Attributes:
        run_id: the run that landed the draft's rows.
        rows: how many rows it landed.
        created: ``False`` when the draft had been committed before, and this
            is that earlier run.
    """

    run_id: UUID
    rows: int
    created: bool


@runtime_checkable
class LoadCommitter(Protocol):
    """Interface for landing drafts in Bronze."""

    def current_version(self, target: DatasetTarget, plant_code: str) -> LoadReference | None:
        """Return the latest completed load of a dataset: its current version.

        The same rule the Silver build uses to pick a snapshot's rows.
        """
        ...

    def landed_by(self, content_hash: str, target: DatasetTarget) -> UUID | None:
        """Return the completed run that landed this file's sheet for this dataset, if any."""
        ...

    def overlapping(
        self, target: DatasetTarget, plant_code: str, start: date, end: date
    ) -> tuple[LoadReference, ...]:
        """Return the completed loads of a dataset whose period meets ``start``..``end``."""
        ...

    def difference(
        self,
        draft_id: UUID,
        base_run_id: UUID | None,
        target: DatasetTarget,
        natural_key: Sequence[str],
    ) -> DifferenceCounts:
        """Count how a draft's rows differ from a load's, by natural key."""
        ...

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
        """Return a page of the rows of one kind of change, ordered by key."""
        ...

    def commit(self, order: CommitOrder, check: Callable[[Draft], None]) -> CommittedLoad:
        """Land a draft in Bronze, in one transaction.

        Args:
            order: what to write.
            check: called with the draft once it is locked; raising refuses
                the commit before anything is written.

        Returns:
            The run, new or, for a draft committed before, the one it has.

        Raises:
            UnknownDraftError: if ``order.owner`` has no live draft of that ID.
            DraftBusyError: if a validation or another commit holds the draft.
            DatasetBusyError: if another commit of the dataset holds its lock.
            CurrentVersionChangedError: if a snapshot's current version is no
                longer ``order.base_run_id``.
            AlreadyLoadedError: if a completed run landed the same file's sheet.
            CommitCountMismatchError: if the rows landed are not the rows staged.
            RepositoryError: if the database refuses a write.
        """
        ...


__all__ = [
    "ColumnChange",
    "CommitOrder",
    "CommittedLoad",
    "DatasetTarget",
    "DifferencePage",
    "DifferenceRow",
    "LoadCommitter",
    "LoadReference",
]
