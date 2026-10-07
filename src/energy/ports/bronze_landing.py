"""Port for landing source rows in Bronze.

Layer: ports

Responsibility:
    Declares how the application records a load in the Bronze layer: the
    archived file it came from, the run that performed it, and the rows it
    landed. The contract encodes the two guarantees a load must keep. First,
    Bronze is append-only: this port can add landing rows and update the run
    ledger, and offers no way to change or remove a landed row. Second, a run is
    all-or-nothing: :meth:`BronzeLandingRepository.land` writes every sheet of
    one load and marks the run completed in a single transaction, so a
    ``completed`` run always has all its rows and a failed one has none. The
    period the rows cover is only known once they have all been read, so it is
    recorded in that same transaction too. This protocol mentions no SQL, no
    session and no table.

Depends on:
    ``energy.domain``, standard library typing only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from datetime import date, datetime
    from uuid import UUID

    from energy.domain.landing import LandingRow


@dataclass(frozen=True, slots=True)
class RawFile:
    """An archived source file, as the catalogue records it.

    Attributes:
        raw_file_id: the catalogue id every landing row of the file points at.
        content_hash: SHA-256 hex digest of the file's bytes.
        bucket: where the file is archived.
        object_key: its key inside the bucket.
    """

    raw_file_id: UUID
    content_hash: str
    bucket: str
    object_key: str


@dataclass(frozen=True, slots=True)
class NewRawFile:
    """A file to add to the catalogue.

    Attributes:
        content_hash: SHA-256 hex digest of the file's bytes.
        bucket: the bucket it was archived into.
        object_key: its key inside the bucket.
        filename: the original file name.
        content_type: MIME type.
        size_bytes: size of the file.
        plant_code: the plant it belongs to.
        source_key: the source it was first loaded for. One workbook can feed
            several sources; the catalogue keeps the first.
        sheet_names: every sheet the workbook contains.
        uploaded_by: who submitted it.
    """

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


@dataclass(frozen=True, slots=True)
class NewRun:
    """A load about to start.

    Attributes:
        flow_name: the name the load runs under.
        source_key: which kind of export it lands.
        source_system: ``qad`` or ``excel``.
        plant_code: the plant it runs for.
        window_start: first date of the business period, when the caller states
            it. ``None`` leaves it to be filled in from the rows on completion.
        window_end: last date of that period, likewise.
        qad_program: the QAD program that produced the export.
        qad_exploration: the QAD exploration name.
        qad_user: the QAD user who ran the query.
        queried_at: when the query ran, timezone-aware.
        search_conditions: the export's search conditions, as delivered.
        parameters: the invocation arguments, which is what a retry replays.
    """

    flow_name: str
    source_key: str
    source_system: str
    plant_code: str
    window_start: date | None
    window_end: date | None
    qad_program: str | None
    qad_exploration: str | None
    qad_user: str | None
    queried_at: datetime | None
    search_conditions: Sequence[Mapping[str, str]]
    parameters: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class LandingTarget:
    """What every row of one load has in common.

    Attributes:
        run_id: the run the rows belong to.
        raw_file_id: the archived file they came from.
        source_key: the source key, which decides the landing table.
        source_system: ``qad`` or ``excel``.
        plant_code: the plant the rows belong to.
    """

    run_id: UUID
    raw_file_id: UUID
    source_key: str
    source_system: str
    plant_code: str


@dataclass(frozen=True, slots=True)
class SheetBatch:
    """The rows of one sheet, ready to land.

    Attributes:
        sheet_name: the worksheet the rows were read from.
        dataset_key: the catalogue name, for hand-maintained sheets only.
        source_uri: where the original file and sheet can be found.
        rows: the landing rows. Read once, lazily, so a large sheet never has to
            be held in memory.
    """

    sheet_name: str
    dataset_key: str | None
    source_uri: str
    rows: Iterable[LandingRow]


@runtime_checkable
class BronzeLandingRepository(Protocol):
    """Interface for the Bronze ledgers and landing tables."""

    def find_file(self, content_hash: str) -> RawFile | None:
        """Look a file up by the hash of its bytes.

        Args:
            content_hash: SHA-256 hex digest.

        Returns:
            The catalogue entry, or ``None`` when the file was never registered.
        """
        ...

    def register_file(self, file: NewRawFile) -> RawFile:
        """Add a file to the catalogue, once per distinct hash.

        Args:
            file: the file to register.

        Returns:
            The catalogue entry. When the hash is already registered the existing
            entry is returned unchanged.

        Raises:
            RepositoryError: if the write fails.
        """
        ...

    def landed_sheets(
        self, raw_file_id: UUID, source_key: str
    ) -> frozenset[tuple[str, str | None]]:
        """Return the sheets of a file that a completed run already landed.

        Only completed runs count: a failed run landed nothing, and a run that is
        still open may yet fail.

        Args:
            raw_file_id: the archived file.
            source_key: the source, which decides the landing table to look in.

        Returns:
            ``(sheet_name, dataset_key)`` pairs. ``dataset_key`` is ``None`` for
            every source that has none.
        """
        ...

    def open_run(self, run: NewRun) -> UUID:
        """Record a run in state ``running``.

        The record is committed at once, so it survives even if landing its rows
        later fails and is rolled back.

        Args:
            run: the run to open.

        Returns:
            The id of the new run.

        Raises:
            RepositoryError: if the write fails.
        """
        ...

    def land(
        self,
        target: LandingTarget,
        sheets: Iterable[SheetBatch],
        period: Callable[[], tuple[date | None, date | None]] | None = None,
    ) -> dict[str, int]:
        """Land every sheet of one load and complete its run, atomically.

        All rows are inserted in one transaction, and the run is set to
        ``completed`` with its row count in that same transaction. If anything
        fails - including an error raised while reading ``sheets`` - nothing is
        landed and the run is left as it was.

        Args:
            target: the run, file and source the rows belong to.
            sheets: the sheets to land, in order.
            period: called once, after every sheet has been read, for the first
                and the last date the rows cover. A date it returns is recorded
                on the run as the run is completed; ``None`` leaves that bound
                as the run was opened with.

        Returns:
            Rows landed per sheet name.

        Raises:
            RepositoryError: if the database rejects the write.
        """
        ...

    def fail_run(self, run_id: UUID, error: str) -> None:
        """Mark a run as ``failed``.

        Args:
            run_id: the run.
            error: what went wrong, kept on the run for diagnosis.
        """
        ...


__all__ = [
    "BronzeLandingRepository",
    "LandingTarget",
    "NewRawFile",
    "NewRun",
    "RawFile",
    "SheetBatch",
]
