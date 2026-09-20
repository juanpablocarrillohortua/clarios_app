"""Application service for file uploads into Bronze.

Layer: services

Responsibility:
    Owns the upload use case end to end: hash the payload, decide whether it is a
    duplicate, archive the original bytes, register an ingestion run, and hand
    back a run id. The ordering is deliberate and is this module's main
    responsibility - the file is archived *before* the run is registered, so a
    crash between the two leaves an orphaned object (harmless, garbage-collected
    later) rather than a run pointing at bytes that were never stored.

Depends on:
    ``energy.domain``, ``energy.ports.object_storage``,
    ``energy.ports.reading_repository``, ``energy.core``.

Implemented in:
    Phase 3 - Ingestion adapters
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from energy.ports.object_storage import ObjectStorage
    from energy.ports.reading_repository import ReadingRepository


@dataclass(frozen=True, slots=True)
class UploadOutcome:
    """Result of accepting one uploaded file.

    Attributes:
        run_id: the ingestion run to poll; ``None`` when the upload was a
            duplicate and no run was started.
        content_hash: SHA-256 of the uploaded bytes.
        object_key: where the original was archived.
        duplicate: whether these exact bytes were already ingested.
        size_bytes: the payload size.
    """

    run_id: UUID | None
    content_hash: str
    object_key: str
    duplicate: bool
    size_bytes: int


class UploadService:
    """Accepts uploaded files, archives them, and schedules their ingestion.

    Args:
        storage: object storage port for archiving originals.
        readings: reading repository port, used to check for duplicates and
            register runs.
        bronze_bucket: bucket archived originals are written to.
    """

    def __init__(
        self,
        storage: ObjectStorage,
        readings: ReadingRepository,
        bronze_bucket: str,
    ) -> None:
        raise NotImplementedError("Phase 3: upload service construction")

    def accept(
        self,
        data: bytes,
        filename: str,
        content_type: str,
        plant_code: str,
        source_key: str,
        uploaded_by: str,
    ) -> UploadOutcome:
        """Accept an uploaded file and schedule its ingestion.

        Args:
            data: the raw uploaded bytes.
            filename: the original filename as submitted.
            content_type: the declared MIME type, cross-checked against the
                payload's magic bytes rather than trusted.
            plant_code: the plant the file belongs to.
            source_key: the configured source it represents.
            uploaded_by: who submitted it, recorded for the audit trail.

        Returns:
            An :class:`UploadOutcome`. A duplicate returns successfully with
            ``duplicate=True`` and no run id, because re-uploading a file is a
            normal user action rather than an error.

        Raises:
            UnsupportedFileFormatError: if the payload is not a readable format.
            UnknownSourceError: if ``source_key`` is not configured.
            EntityNotFoundError: if ``plant_code`` is unknown.
        """
        raise NotImplementedError("Phase 3: upload acceptance workflow")

    def compute_hash(self, data: bytes) -> str:
        """Compute the content hash used for duplicate detection.

        Args:
            data: the raw payload.

        Returns:
            A lowercase SHA-256 hex digest.
        """
        raise NotImplementedError("Phase 3: content hashing")

    def detect_format(self, data: bytes, declared_content_type: str) -> str:
        """Determine the real format of an upload from its bytes.

        The declared content type comes from the browser and is routinely wrong;
        trusting it means a renamed ``.txt`` reaches the Excel parser and fails
        with an unhelpful error deep inside a pipeline.

        Args:
            data: the raw payload.
            declared_content_type: what the client claimed.

        Returns:
            The detected format: ``"xlsx"``, ``"xls"``, or ``"csv"``.

        Raises:
            UnsupportedFileFormatError: if the payload matches no known format.
        """
        raise NotImplementedError("Phase 3: magic-byte format detection")


__all__ = ["UploadOutcome", "UploadService"]
