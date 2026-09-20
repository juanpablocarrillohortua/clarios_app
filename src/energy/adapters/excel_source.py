"""Adapter reading plant consumption workbooks into Bronze.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.energy_source.EnergySource` for the Excel
    workbooks plant staff maintain by hand. This is the messiest source the
    platform has: merged cells, headers that move between revisions, totals rows
    mixed into the data, dates as text, and numbers with locale-specific decimal
    separators. All of that tolerance lives here and nowhere else - the adapter
    returns a rectangular frame of raw values, and everything downstream may
    assume clean structure. It performs no unit conversion, no timezone
    normalisation beyond recording the declared source timezone, and no
    filtering of implausible values; those are Silver concerns.

Depends on:
    ``energy.ports.energy_source``, ``energy.ports.object_storage``,
    ``energy.domain.exceptions``, ``energy.core``, ``polars``, ``openpyxl``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from energy.ports.energy_source import SourceBatch, SourceDescriptor

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    import polars as pl

    from energy.domain.value_objects import TimeWindow
    from energy.ports.object_storage import ObjectStorage


@dataclass(frozen=True, slots=True)
class SheetLayout:
    """How to find the data inside one worksheet.

    Declared per source in ``config/sources.yaml`` so that a workbook revision is
    a configuration change rather than a code change.

    Attributes:
        sheet_name: worksheet to read. A name rather than an index, because plant
            staff reorder tabs.
        header_row: 1-based row holding the column headers.
        first_data_row: 1-based row where data starts, which may not be
            ``header_row + 1`` when the sheet has a units row beneath the header.
        column_map: source header text to canonical field name. Headers not
            listed are ignored rather than being an error, so an extra local
            column does not break ingestion.
        timestamp_column: canonical field holding the reading timestamp.
        skip_rows_matching: regular expressions; a row whose first cell matches
            any of them is dropped. Used to remove ``TOTAL`` and subtotal rows
            that would otherwise be ingested as readings and double the plant's
            consumption.
    """

    sheet_name: str
    header_row: int
    first_data_row: int
    column_map: Mapping[str, str]
    timestamp_column: str
    skip_rows_matching: tuple[str, ...]


class ExcelSource:
    """Reads a consumption workbook and returns its raw rows.

    Args:
        descriptor: the configured source identity.
        layout: where the data sits inside the workbook.
        storage: object storage holding the archived original file.
        bucket: bucket the workbook was archived into.
        object_key: key of the workbook to read.
    """

    def __init__(
        self,
        descriptor: SourceDescriptor,
        layout: SheetLayout,
        storage: ObjectStorage,
        bucket: str,
        object_key: str,
    ) -> None:
        raise NotImplementedError("Phase 3: Excel source construction")

    @property
    def descriptor(self) -> SourceDescriptor:
        """Return this source's static identity and capabilities."""
        raise NotImplementedError("Phase 3: descriptor accessor")

    def health_check(self) -> bool:
        """Confirm the archived workbook is present and readable.

        Returns:
            ``True`` when the object exists and its first sheet can be opened.

        Raises:
            SourceUnavailableError: if object storage is unreachable.
            EntityNotFoundError: if the object key does not exist.
        """
        raise NotImplementedError("Phase 3: workbook reachability check")

    def fetch(self, window: TimeWindow | None = None) -> SourceBatch:
        """Read the workbook and return its rows verbatim.

        Excel sources are not incrementally queryable: the whole sheet is read
        and ``window`` is applied as a post-filter, because a hand-edited
        workbook may revise any historical row at any time.

        Args:
            window: period to restrict to after reading; ``None`` returns
                everything found.

        Returns:
            A :class:`~energy.ports.energy_source.SourceBatch` whose frame holds
            the raw cell values with only the column renaming from
            ``layout.column_map`` applied.

        Raises:
            UnsupportedFileFormatError: if the file is not a readable workbook.
            SchemaValidationError: if the expected sheet or header row is absent,
                which means the workbook revision no longer matches its layout.
        """
        raise NotImplementedError("Phase 3: workbook reading")

    def fetch_chunks(
        self,
        window: TimeWindow | None = None,
        chunk_size: int = 100_000,
    ) -> Iterator[SourceBatch]:
        """Read the workbook in row chunks.

        Args:
            window: period to restrict to after reading.
            chunk_size: maximum rows per yielded batch.

        Yields:
            Successive batches, each hashing only its own rows.

        Raises:
            UnsupportedFileFormatError: if the file is not a readable workbook.
        """
        raise NotImplementedError("Phase 3: chunked workbook reading")

    def _locate_header(self, raw: pl.DataFrame) -> int:
        """Find the header row when it has drifted from its configured position.

        Plant staff insert title and logo rows above the table. Rather than
        failing, the adapter searches a small window around ``header_row`` for a
        row containing the expected headers.

        Args:
            raw: the sheet read with no header interpretation.

        Returns:
            The 0-based index of the detected header row.

        Raises:
            SchemaValidationError: if no row within the search window contains
                the expected headers.
        """
        raise NotImplementedError("Phase 3: header row detection")


def read_workbook(path: Path, layout: SheetLayout) -> pl.DataFrame:
    """Read one worksheet from a local file into a frame.

    Standalone helper used by the adapter and by ``scripts`` for inspecting a
    problem workbook without setting up object storage.

    Args:
        path: local path to the ``.xlsx`` file.
        layout: where the data sits inside it.

    Returns:
        The raw rows, with the configured column renaming applied and skip rows
        removed.

    Raises:
        UnsupportedFileFormatError: if the file cannot be opened as a workbook.
        SchemaValidationError: if the configured sheet is missing.
    """
    raise NotImplementedError("Phase 3: standalone workbook reader")


__all__ = ["ExcelSource", "SheetLayout", "read_workbook"]
