"""Port for reading the loads Bronze has recorded: the history and the detail of each.

Layer: ports

Responsibility:
    Declares how the web app reads ``bronze.ingestion_runs`` as a history of
    loads: every load, whichever way it came (the web app or the command
    line), filtered, sorted and paged, and one load in detail with the file it
    came from and how many rows it landed from each sheet. It only reads, and
    only the loads of the plants a caller may see.

Depends on:
    ``energy.domain``, ``energy.ports.load_committer``, standard library
    typing only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date, datetime
    from uuid import UUID

    from energy.domain.draft_commit import LoadOrigin
    from energy.ports.load_committer import DatasetTarget, LoadReference


class LoadSort(StrEnum):
    """What a history is sorted by."""

    STARTED_AT = "started_at"
    ROWS = "rows"


@dataclass(frozen=True, slots=True)
class LoadFilter:
    """Which loads to list. ``None`` means any.

    Attributes:
        plant_scope: the plants the caller may see; empty means none.
        target: one dataset, by where its rows land.
        state: one run state.
        uploaded_by: one person, by subject.
        origin: the web app or the command line.
        period_from: loads whose period reaches this day or later.
        period_to: loads whose period begins this day or earlier.
        started_from: loads started on this day or later.
        started_to: loads started on this day or earlier.
    """

    plant_scope: tuple[str, ...]
    target: DatasetTarget | None = None
    state: str | None = None
    uploaded_by: str | None = None
    origin: LoadOrigin | None = None
    period_from: date | None = None
    period_to: date | None = None
    started_from: date | None = None
    started_to: date | None = None


@dataclass(frozen=True, slots=True)
class SheetLoaded:
    """One sheet of a load.

    Attributes:
        name: the worksheet.
        dataset_key: the catalogue's name, for hand-maintained sheets.
        rows: the rows landed from it; ``None`` in a list, where it is not counted.
    """

    name: str
    dataset_key: str | None
    rows: int | None = None


@dataclass(frozen=True, slots=True)
class LoadSummary:
    """One load, as a history lists it.

    Attributes:
        run_id: the run.
        origin: the web app or the command line.
        state: the run's state.
        source_key: the Bronze source.
        dataset_key: the dataset spec, for a load from the web app.
        sheets: the sheets it landed.
        filename: the file it came from.
        rows: the rows it landed.
        started_at: when it started.
        finished_at: when it ended; ``None`` while it runs.
        window_start: the first date its rows cover, for movements.
        window_end: the last date, likewise.
        uploaded_by: who loaded it, by subject; ``system`` from the command line.
        uploaded_by_name: their name, for display.
        warnings_accepted: rows with warnings its owner accepted.
        error: why it failed, for a failed run.
    """

    run_id: UUID
    origin: LoadOrigin
    state: str
    source_key: str
    dataset_key: str | None
    sheets: tuple[SheetLoaded, ...]
    filename: str | None
    rows: int
    started_at: datetime
    finished_at: datetime | None
    window_start: date | None
    window_end: date | None
    uploaded_by: str
    uploaded_by_name: str | None
    warnings_accepted: int
    error: str


@dataclass(frozen=True, slots=True)
class ArchivedOriginal:
    """Where the file a load came from is archived."""

    bucket: str
    object_key: str
    filename: str
    content_type: str
    size_bytes: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class LoadRecord:
    """One load in detail.

    Attributes:
        summary: what the history shows of it, with each sheet's rows counted.
        plant_code: the plant.
        flow_name: the name it was recorded under.
        original: the archived file; ``None`` if Bronze does not catalogue it.
        parameters: the run's parameters, as recorded.
    """

    summary: LoadSummary
    plant_code: str
    flow_name: str
    original: ArchivedOriginal | None
    parameters: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Person:
    """Someone who loaded data through the web app."""

    subject: str
    name: str | None


@dataclass(frozen=True, slots=True)
class LoadPage:
    """A page of a history.

    Attributes:
        total: how many loads match the filter.
        items: this page of them.
        people: everyone who loaded through the web app, for the person filter.
    """

    total: int
    items: tuple[LoadSummary, ...]
    people: tuple[Person, ...]


@runtime_checkable
class LoadLedger(Protocol):
    """Interface for reading Bronze's record of loads."""

    def loads(
        self, wanted: LoadFilter, sort: LoadSort, descending: bool, offset: int, limit: int
    ) -> LoadPage:
        """Return a page of loads."""
        ...

    def load(self, run_id: UUID, plant_scope: Sequence[str]) -> LoadRecord | None:
        """Return one load of the caller's plants, or ``None``."""
        ...

    def last_loads(
        self, targets: Sequence[DatasetTarget], plant_scope: Sequence[str]
    ) -> Mapping[DatasetTarget, LoadReference]:
        """Return the latest completed load of each dataset that has one."""
        ...


__all__ = [
    "ArchivedOriginal",
    "LoadFilter",
    "LoadLedger",
    "LoadPage",
    "LoadRecord",
    "LoadSort",
    "LoadSummary",
    "Person",
    "SheetLoaded",
]
