"""Drafts: data a person is preparing before it lands in Bronze.

Layer: domain

Responsibility:
    Defines what a draft is and the limits an uploaded file must respect. A
    draft is one upload of one ``.xlsx`` file for one dataset spec. Its
    original is archived as it arrives; validation then reads it back and
    stages its rows and the issues found, in schema ``staging``. Nothing of a
    draft reaches Bronze until a later increment commits it, so staging is a
    working area, never evidence.

    The limits live here so the API, the browser (through ``GET /datasets``)
    and the Caddyfile (through a test) all use the same numbers:

    * a file may be up to :data:`MAX_FILE_BYTES` (300 MiB, which Windows shows
      as "300 MB");
    * the API validates a file of up to :data:`CHECK_IN_REQUEST_MAX_BYTES`
      itself; a larger one waits for the worker of increment 12.

    :class:`UploadGuard` watches the bytes of an upload as they arrive: it
    counts them, refuses the first byte past the limit and checks that the file
    begins as a zip archive, which every ``.xlsx`` is.

    The module is pure. It reads no file and holds no connection.

Depends on:
    ``energy.domain.draft_rules``, ``energy.domain.exceptions``, standard
    library only.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Final

from energy.domain.exceptions import (
    EmptyFileError,
    FileTooLargeError,
    IncompleteUploadError,
    InvalidFilenameError,
    LengthRequiredError,
    UnsupportedFileFormatError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date, datetime
    from uuid import UUID

    from energy.domain.draft_rules import RuleCount

MIB: Final = 1024 * 1024

#: The largest file a person may upload: 300 MiB, shown by Windows as "300 MB".
MAX_FILE_BYTES: Final = 300 * MIB
#: The largest file the API validates inside the request. A workbook needs
#: about 16 MB of memory for each MB of file (docs/frontend-plan.md §6).
CHECK_IN_REQUEST_MAX_BYTES: Final = 5 * MIB
#: How long a draft is kept after it was last worked on (question 17).
DRAFT_RETENTION: Final = timedelta(days=30)
#: The size of each part an upload is sent to object storage in. The MinIO
#: client builds a part by appending chunks and then copies it once more, so
#: an upload holds about four parts at its peak: 16 MiB parts took 80 MiB for
#: a 300 MiB file, 8 MiB parts 34 MiB (measured 2026-10-17). S3 allows 5 MiB
#: at least and 10,000 parts, so 300 MiB is 38 parts.
UPLOAD_PART_BYTES: Final = 8 * MIB

XLSX_SUFFIX: Final = ".xlsx"
XLSX_MEDIA_TYPE: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
#: What a browser may declare for an ``.xlsx``. Nothing trusts it: the bytes
#: are checked too.
ACCEPTED_MEDIA_TYPES: Final = frozenset({XLSX_MEDIA_TYPE, "application/octet-stream", ""})
#: How every zip archive, and so every ``.xlsx``, begins.
ZIP_MAGIC: Final = b"PK\x03\x04"
#: Where an upload is written while its hash is not yet known.
INCOMING_PREFIX: Final = "incoming/"

_MAX_FILENAME_LENGTH: Final = 255


class DraftStatus(StrEnum):
    """Where a draft stands.

    ``receiving`` while its bytes arrive; ``uploaded`` once the original is
    archived; ``validated`` once its rows were checked; ``committed`` once its
    rows landed in Bronze (increment 7); ``failed`` when the upload broke off
    or was refused.
    """

    RECEIVING = "receiving"
    UPLOADED = "uploaded"
    VALIDATED = "validated"
    COMMITTED = "committed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExportFacts:
    """What a workbook says about itself, read at validation for the commit.

    The run a commit opens records them exactly as the command-line loader
    records them for the same file.

    Attributes:
        sheet_names: every sheet of the workbook, in its order.
        window_start: the first date of the period column; ``None`` for a
            snapshot, or when no cell of that column is a date.
        window_end: the last date of it, likewise.
        export_info: what the "Info" sheet of a QAD export says about the
            query; empty for a workbook without one.
        search_conditions: the rows of its "Condiciones de búsqueda" sheet, as
            delivered; empty without one.
    """

    sheet_names: tuple[str, ...]
    window_start: date | None = None
    window_end: date | None = None
    export_info: Mapping[str, str] = field(default_factory=dict)
    search_conditions: tuple[Mapping[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationSummary:
    """What the last validation of a draft found.

    Attributes:
        sheet_name: the sheet that was read.
        header_row: the row its headers were read from.
        rows_read: the data rows read, as Bronze would keep them.
        rows_with_errors: rows with at least one error.
        rows_with_warnings: rows with at least one warning.
        counts: for each rule and column, how many rows broke it and how many
            of those issues are listed.
        spec_digest: the digest of the spec the file was checked against
            (:func:`energy.domain.dataset_spec.spec_digest`); ``None`` for a
            validation older than revision 0008.
        facts: what the workbook says about itself; ``None`` for a validation
            older than revision 0009, or of a file that could not be read.
    """

    sheet_name: str
    header_row: int
    rows_read: int
    rows_with_errors: int
    rows_with_warnings: int
    counts: tuple[RuleCount, ...]
    spec_digest: str | None = None
    facts: ExportFacts | None = None

    @property
    def blocking(self) -> bool:
        """Return whether an error stops this draft from being loaded."""
        return any(count.severity == "error" for count in self.counts)


@dataclass(frozen=True, slots=True)
class Draft:
    """One upload of one file, while it is prepared.

    Attributes:
        draft_id: the draft's identifier.
        dataset_key: the dataset spec it is checked against.
        source_key: the Bronze source of that spec.
        plant_code: the plant the data belongs to.
        status: where the draft stands.
        filename: the file's name as the person gave it. Data only: it never
            becomes a key or a path.
        declared_bytes: the size the upload announced.
        size_bytes: the size received; ``None`` until the upload is complete.
        content_hash: SHA-256 of the file; ``None`` until then.
        bucket: where the original is archived; ``None`` until then.
        object_key: its key there; ``None`` until then.
        created_by: the person, as the identity provider's subject.
        created_by_name: their name, for display.
        created_at: when the upload began.
        uploaded_at: when the original was archived.
        validated_at: when it was last validated.
        expires_at: when an abandoned draft is purged.
        failure_code: why the upload failed, for a ``failed`` draft.
        validation: what the last validation found; ``None`` before one.
        previous_draft_id: the draft whose file this one corrects, if it was
            uploaded as a correction. The earlier draft may since have
            expired or been purged.
        run_id: for a committed draft, the Bronze run that landed its rows.
        committed_at: when it was committed.
    """

    draft_id: UUID
    dataset_key: str
    source_key: str
    plant_code: str
    status: DraftStatus
    filename: str
    declared_bytes: int
    size_bytes: int | None
    content_hash: str | None
    bucket: str | None
    object_key: str | None
    created_by: str
    created_by_name: str | None
    created_at: datetime
    uploaded_at: datetime | None
    validated_at: datetime | None
    expires_at: datetime
    failure_code: str | None
    validation: ValidationSummary | None
    previous_draft_id: UUID | None = None
    run_id: UUID | None = None
    committed_at: datetime | None = None


def clean_filename(raw: str) -> str:
    """Return a file name fit to keep and show.

    Only the last segment of a path is kept, in Unicode NFC, without control
    or formatting characters, and at most 255 characters long, the extension
    kept. The name is data: no key or path is ever built from it.

    Args:
        raw: the name the client sent.

    Returns:
        The cleaned name.

    Raises:
        InvalidFilenameError: if nothing usable is left.
    """
    name = unicodedata.normalize("NFC", raw).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(char for char in name if not unicodedata.category(char).startswith("C"))
    name = name.strip()
    if not name or name in {".", ".."}:
        raise InvalidFilenameError("the upload names no file")
    if len(name) > _MAX_FILENAME_LENGTH:
        suffix = PurePosixPath(name).suffix
        name = name[: _MAX_FILENAME_LENGTH - len(suffix)] + suffix
    return name


def require_xlsx(filename: str, media_type: str) -> None:
    """Refuse a file that does not say it is an ``.xlsx``.

    Args:
        filename: the cleaned file name.
        media_type: the content type the client declared, without parameters.

    Raises:
        UnsupportedFileFormatError: for any other extension or declared type.
    """
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix != XLSX_SUFFIX:
        raise UnsupportedFileFormatError("only .xlsx files are accepted", suffix=suffix)
    if media_type.strip().lower() not in ACCEPTED_MEDIA_TYPES:
        raise UnsupportedFileFormatError(
            "the declared content type is not that of an .xlsx file", content_type=media_type
        )


def check_declared_size(declared: int | None, limit: int = MAX_FILE_BYTES) -> int:
    """Check the size an upload announces, before a byte of it is read.

    Args:
        declared: the request's ``Content-Length``; ``None`` when absent.
        limit: the largest size accepted.

    Returns:
        The declared size.

    Raises:
        LengthRequiredError: if the size is not announced.
        EmptyFileError: if it is zero.
        FileTooLargeError: if it is over the limit.
    """
    if declared is None:
        raise LengthRequiredError("the upload must state its size")
    if declared <= 0:
        raise EmptyFileError("the file is empty")
    if declared > limit:
        raise FileTooLargeError("the file is larger than the limit", size=declared, limit=limit)
    return declared


def incoming_key(draft_id: UUID) -> str:
    """Return the key an upload is written under until its hash is known."""
    return f"{INCOMING_PREFIX}{draft_id}"


class UploadGuard:
    """Watch the bytes of one upload as they arrive.

    Args:
        declared_bytes: the size the upload announced.
        limit: the largest size accepted.
    """

    __slots__ = ("_declared", "_head", "_limit", "received")

    def __init__(self, declared_bytes: int, limit: int = MAX_FILE_BYTES) -> None:
        self._declared = declared_bytes
        self._limit = limit
        self._head = b""
        self.received = 0

    def observe(self, chunk: bytes) -> None:
        """Take one chunk into account.

        Args:
            chunk: the bytes that just arrived.

        Raises:
            FileTooLargeError: at the first byte past the limit.
            IncompleteUploadError: at the first byte past the announced size.
            UnsupportedFileFormatError: as soon as the first four bytes show
                that the file is not a zip archive.
        """
        self.received += len(chunk)
        if self.received > self._limit:
            raise FileTooLargeError("the file is larger than the limit", limit=self._limit)
        if self.received > self._declared:
            raise IncompleteUploadError(
                "the upload is longer than it announced", declared=self._declared
            )
        missing = len(ZIP_MAGIC) - len(self._head)
        if missing > 0:
            self._head += chunk[:missing]
            if len(self._head) == len(ZIP_MAGIC) and self._head != ZIP_MAGIC:
                raise UnsupportedFileFormatError(
                    "the file is not an .xlsx: it is not a zip archive"
                )

    def finish(self) -> None:
        """Check the upload once its last byte has arrived.

        Raises:
            IncompleteUploadError: if fewer bytes arrived than were announced.
            UnsupportedFileFormatError: if the file is too short to be a zip.
        """
        if self.received != self._declared:
            raise IncompleteUploadError(
                "the upload is shorter than it announced",
                declared=self._declared,
                received=self.received,
            )
        if self._head != ZIP_MAGIC:
            raise UnsupportedFileFormatError("the file is not an .xlsx: it is not a zip archive")


__all__ = [
    "ACCEPTED_MEDIA_TYPES",
    "CHECK_IN_REQUEST_MAX_BYTES",
    "DRAFT_RETENTION",
    "INCOMING_PREFIX",
    "MAX_FILE_BYTES",
    "MIB",
    "UPLOAD_PART_BYTES",
    "XLSX_MEDIA_TYPE",
    "XLSX_SUFFIX",
    "ZIP_MAGIC",
    "Draft",
    "DraftStatus",
    "ExportFacts",
    "UploadGuard",
    "ValidationSummary",
    "check_declared_size",
    "clean_filename",
    "incoming_key",
    "require_xlsx",
]
