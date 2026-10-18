"""Port for S3-compatible object storage.

Layer: ports

Responsibility:
    Declares how the application stores and retrieves immutable blobs: the exact
    bytes of every uploaded workbook and ERP extract, and the generated report
    artefacts. Keeping the original file is what makes Bronze genuinely
    reproducible - a Bronze row records a ``source_uri`` pointing here, so any
    downstream number can be traced back to the literal file a plant sent. This
    protocol never mentions MinIO, boto3, or bucket policies; those belong to
    :mod:`energy.adapters.minio_storage`.

Depends on:
    ``energy.domain``, standard library typing only.

Streaming:
    :meth:`ObjectStorage.put_stream` and :meth:`ObjectStorage.download_to`
    pass a file through a piece at a time, for uploads of up to 300 MB that
    must never be held in memory. :meth:`ObjectStorage.copy` lets an upload
    written under a temporary key move to the key that carries its hash
    without its bytes passing through the application.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from datetime import datetime, timedelta
    from io import BufferedReader
    from typing import BinaryIO


@runtime_checkable
class ByteSource(Protocol):
    """Something bytes can be read from, a piece at a time: a file, an upload."""

    def read(self, size: int = -1, /) -> bytes:
        """Return up to ``size`` bytes; an empty result means the end."""
        ...


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Metadata describing one object in the store.

    Attributes:
        key: object key within the bucket, unique and stable.
        size_bytes: size of the stored payload.
        content_hash: SHA-256 hex digest of the payload, written at upload time
            and verified on read.
        content_type: MIME type, for example
            ``"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"``.
        stored_at: when the object was written, timezone-aware UTC.
        metadata: user metadata attached at upload, carrying at minimum the
            plant code, source key, and ingestion run id.
    """

    key: str
    size_bytes: int
    content_hash: str
    content_type: str
    stored_at: datetime
    metadata: Mapping[str, str]


@runtime_checkable
class ObjectStorage(Protocol):
    """Interface for storing and retrieving immutable blobs."""

    def ensure_bucket(self, bucket: str) -> None:
        """Create the bucket if it does not exist.

        Idempotent: calling it on an existing bucket is a no-op. Called at
        startup so a fresh environment does not fail on first upload.

        Args:
            bucket: bucket name.

        Raises:
            SourceUnavailableError: if the storage backend is unreachable.
        """
        ...

    def put(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StoredObject:
        """Store a payload.

        Objects are write-once: uploading to an existing key must raise rather
        than overwrite, because a Bronze row already points at those bytes and
        replacing them would silently rewrite history.

        Args:
            bucket: target bucket.
            key: object key. Convention is
                ``{plant_code}/{source_key}/{YYYY}/{MM}/{DD}/{content_hash}.{ext}``,
                which makes the key itself carry the provenance and makes a
                duplicate upload collide by construction.
            data: the raw bytes exactly as received.
            content_type: MIME type of the payload.
            metadata: user metadata to attach.

        Returns:
            The :class:`StoredObject` describing what was written.

        Raises:
            SourceUnavailableError: if the backend is unreachable.
            FileExistsError: if ``key`` already exists.
        """
        ...

    def put_stream(
        self,
        bucket: str,
        key: str,
        source: ByteSource,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StoredObject:
        """Store a payload of unknown length, a part at a time.

        The payload is never held whole: it is sent in parts of a fixed size
        and hashed on the way. If reading ``source`` raises, nothing is left
        under ``key`` and the error propagates. Write-once, as :meth:`put`.

        Args:
            bucket: target bucket.
            key: object key.
            source: where the bytes are read from, until it returns ``b""``.
            content_type: MIME type of the payload.
            metadata: user metadata to attach.

        Returns:
            The :class:`StoredObject` written, with its size and SHA-256.

        Raises:
            SourceUnavailableError: if the backend is unreachable.
            FileExistsError: if ``key`` already exists.
        """
        ...

    def copy(
        self,
        bucket: str,
        source_key: str,
        target_key: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StoredObject:
        """Copy an object inside a bucket, without passing its bytes through here.

        Write-once, as :meth:`put`: the target must not exist.

        Args:
            bucket: the bucket of both objects.
            source_key: the object to copy.
            target_key: the key of the copy.
            metadata: the copy's user metadata, replacing the source's. Must
                include ``content-hash`` for :meth:`get` to verify the copy.

        Returns:
            The :class:`StoredObject` written.

        Raises:
            EntityNotFoundError: if the source does not exist.
            FileExistsError: if the target already exists.
        """
        ...

    def delete(self, bucket: str, key: str) -> None:
        """Remove an object. Removing one that is not there is not an error.

        Args:
            bucket: source bucket.
            key: object key.

        Raises:
            SourceUnavailableError: if the backend is unreachable.
        """
        ...

    def download_to(self, bucket: str, key: str, target: BinaryIO) -> StoredObject:
        """Copy an object's bytes into a file, a piece at a time, checking them.

        Args:
            bucket: source bucket.
            key: object key.
            target: an open binary file to write to.

        Returns:
            The object's metadata.

        Raises:
            EntityNotFoundError: if the key does not exist.
            ContentHashMismatchError: if the bytes do not hash to the digest
                recorded at upload time.
        """
        ...

    def get(self, bucket: str, key: str) -> bytes:
        """Retrieve a payload in full.

        Args:
            bucket: source bucket.
            key: object key.

        Returns:
            The stored bytes.

        Raises:
            EntityNotFoundError: if the key does not exist.
            ContentHashMismatchError: if the retrieved bytes do not hash to the
                digest recorded at upload time, which means the object was
                corrupted or tampered with.
        """
        ...

    def open_stream(self, bucket: str, key: str) -> BufferedReader:
        """Open a payload for streaming reads.

        Used for workbooks too large to hold in memory.

        Args:
            bucket: source bucket.
            key: object key.

        Returns:
            A binary reader the caller is responsible for closing.

        Raises:
            EntityNotFoundError: if the key does not exist.
        """
        ...

    def stat(self, bucket: str, key: str) -> StoredObject:
        """Return an object's metadata without downloading it.

        Args:
            bucket: source bucket.
            key: object key.

        Returns:
            The :class:`StoredObject` metadata.

        Raises:
            EntityNotFoundError: if the key does not exist.
        """
        ...

    def exists(self, bucket: str, key: str) -> bool:
        """Return whether a key is present.

        Args:
            bucket: source bucket.
            key: object key.

        Returns:
            ``True`` when the object exists. Used to skip re-ingesting a file
            whose content hash is already known.
        """
        ...

    def list_objects(self, bucket: str, prefix: str = "") -> Iterator[StoredObject]:
        """Enumerate objects under a prefix.

        Args:
            bucket: source bucket.
            prefix: key prefix to filter by, for example ``"MTY-01/excel/2026/"``.

        Yields:
            :class:`StoredObject` metadata, lazily, so a bucket with millions of
            keys can be walked without exhausting memory.
        """
        ...

    def presigned_url(self, bucket: str, key: str, expires_in: timedelta) -> str:
        """Generate a time-limited download URL.

        Lets the API hand a browser a direct link to a generated report without
        proxying the bytes or exposing storage credentials.

        Args:
            bucket: source bucket.
            key: object key.
            expires_in: how long the URL stays valid.

        Returns:
            A signed URL.

        Raises:
            EntityNotFoundError: if the key does not exist.
        """
        ...


__all__ = ["ByteSource", "ObjectStorage", "StoredObject"]
