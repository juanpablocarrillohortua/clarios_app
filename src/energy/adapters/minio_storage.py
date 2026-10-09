"""MinIO implementation of the object storage port.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.object_storage.ObjectStorage` against a
    MinIO or any S3-compatible endpoint. It is the only module in the platform
    that knows about buckets, credentials, and presigned URLs. Two guarantees it
    owns: objects are written once and never overwritten, and every payload is
    verified against its recorded SHA-256 on read, so a Bronze row's
    ``source_uri`` always resolves to the exact bytes that produced it.

Depends on:
    ``energy.ports.object_storage``, ``energy.domain``,
    ``energy.core.settings``, ``minio``.
"""

from __future__ import annotations

import hashlib
import io
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import certifi
import urllib3
from minio import Minio
from minio.error import MinioException, S3Error
from urllib3.exceptions import HTTPError

from energy.domain.exceptions import (
    ContentHashMismatchError,
    EntityNotFoundError,
    SourceUnavailableError,
)
from energy.domain.landing import archive_object_key
from energy.ports.object_storage import StoredObject

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from datetime import timedelta
    from io import BufferedReader

    from energy.core.settings import MinioSettings

# S3 returns user metadata as HTTP headers carrying this prefix.
_USER_METADATA_PREFIX: Final = "x-amz-meta-"
_CONTENT_HASH_KEY: Final = "content-hash"
_NOT_FOUND_CODES: Final = frozenset({"NoSuchKey", "NoSuchObject", "NoSuchBucket"})
_DEFAULT_CONTENT_TYPE: Final = "application/octet-stream"
_CONNECT_TIMEOUT_SECONDS: Final = 10.0
_READ_TIMEOUT_SECONDS: Final = 300.0
_RETRIES: Final = 2


@contextmanager
def _translated(bucket: str, key: str | None = None) -> Iterator[None]:
    """Turn MinIO and transport errors into the domain's own."""
    try:
        yield
    except S3Error as exc:
        if exc.code in _NOT_FOUND_CODES:
            raise EntityNotFoundError("object not found", bucket=bucket, key=key) from exc
        raise SourceUnavailableError(
            "object storage rejected the request", bucket=bucket, key=key, reason=exc.code
        ) from exc
    except (MinioException, HTTPError, OSError) as exc:
        raise SourceUnavailableError(
            "object storage is unreachable", bucket=bucket, key=key, reason=str(exc)
        ) from exc


def _http_client() -> urllib3.PoolManager:
    """Build the HTTP pool the MinIO client talks through.

    The client's own default waits five minutes to connect and retries five
    times, so a wrong endpoint takes the better part of a minute to say so.
    These limits fail in seconds while still riding out a brief 5xx.
    """
    return urllib3.PoolManager(
        timeout=urllib3.Timeout(connect=_CONNECT_TIMEOUT_SECONDS, read=_READ_TIMEOUT_SECONDS),
        maxsize=10,
        cert_reqs="CERT_REQUIRED",
        ca_certs=certifi.where(),
        retries=urllib3.Retry(
            total=_RETRIES, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504]
        ),
    )


class MinioObjectStorage:
    """S3-compatible object storage backed by MinIO.

    Args:
        settings: endpoint, credentials, and default bucket names.
    """

    def __init__(self, settings: MinioSettings) -> None:
        self._client = Minio(
            settings.endpoint,
            access_key=settings.access_key,
            secret_key=settings.secret_key.get_secret_value(),
            secure=settings.secure,
            region=settings.region,
            http_client=_http_client(),
        )

    def ensure_bucket(self, bucket: str) -> None:
        """Create the bucket if it does not exist.

        Write-once is enforced by :meth:`put`, not by a bucket policy: object
        locking has to be chosen when a bucket is created and cannot be added to
        one that already holds data.

        Args:
            bucket: bucket name.

        Raises:
            SourceUnavailableError: if MinIO is unreachable.
        """
        with _translated(bucket):
            if not self._client.bucket_exists(bucket):
                self._client.make_bucket(bucket)

    def put(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StoredObject:
        """Store a payload, refusing to overwrite an existing key.

        Args:
            bucket: target bucket.
            key: object key, conventionally containing the content hash.
            data: the raw bytes exactly as received.
            content_type: MIME type of the payload.
            metadata: user metadata to attach; the content hash is added
                automatically. Values travel as HTTP headers, so they must be
                ASCII.

        Returns:
            The :class:`~energy.ports.object_storage.StoredObject` written.

        Raises:
            FileExistsError: if the key is already present.
            SourceUnavailableError: if MinIO is unreachable.
        """
        if self.exists(bucket, key):
            raise FileExistsError(f"object already exists: {bucket}/{key}")

        content_hash = hashlib.sha256(data).hexdigest()
        user_metadata: dict[str, str] = {**(metadata or {}), _CONTENT_HASH_KEY: content_hash}
        with _translated(bucket, key):
            self._client.put_object(
                bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
                metadata=dict(user_metadata),
            )
        return StoredObject(
            key=key,
            size_bytes=len(data),
            content_hash=content_hash,
            content_type=content_type,
            stored_at=datetime.now(UTC),
            metadata=user_metadata,
        )

    def get(self, bucket: str, key: str) -> bytes:
        """Retrieve a payload and verify its integrity.

        Args:
            bucket: source bucket.
            key: object key.

        Returns:
            The stored bytes.

        Raises:
            EntityNotFoundError: if the key does not exist.
            ContentHashMismatchError: if the payload does not hash to the digest
                recorded at upload time.
        """
        recorded = self.stat(bucket, key)
        with _translated(bucket, key):
            response = self._client.get_object(bucket, key)
            try:
                data: bytes = response.read()
            finally:
                response.close()
                response.release_conn()

        actual = hashlib.sha256(data).hexdigest()
        if recorded.content_hash and actual != recorded.content_hash:
            raise ContentHashMismatchError(
                "stored object does not match its recorded hash",
                bucket=bucket,
                key=key,
                expected=recorded.content_hash,
                actual=actual,
            )
        return data

    def open_stream(self, bucket: str, key: str) -> BufferedReader:
        """Open a payload for streaming reads.

        Args:
            bucket: source bucket.
            key: object key.

        Returns:
            A binary reader the caller must close.

        Raises:
            EntityNotFoundError: if the key does not exist.
        """
        with _translated(bucket, key):
            response = self._client.get_object(bucket, key)
        return io.BufferedReader(response)

    def stat(self, bucket: str, key: str) -> StoredObject:
        """Return an object's metadata without downloading it.

        Args:
            bucket: source bucket.
            key: object key.

        Returns:
            The object metadata. ``content_hash`` is empty for an object that
            was not written through :meth:`put`.

        Raises:
            EntityNotFoundError: if the key does not exist.
        """
        with _translated(bucket, key):
            found = self._client.stat_object(bucket, key)

        user_metadata = {
            str(name).lower().removeprefix(_USER_METADATA_PREFIX): str(value)
            for name, value in dict(found.metadata or {}).items()
            if str(name).lower().startswith(_USER_METADATA_PREFIX)
        }
        return StoredObject(
            key=key,
            size_bytes=int(found.size or 0),
            content_hash=user_metadata.get(_CONTENT_HASH_KEY, ""),
            content_type=found.content_type or _DEFAULT_CONTENT_TYPE,
            stored_at=found.last_modified or datetime.now(UTC),
            metadata=user_metadata,
        )

    def exists(self, bucket: str, key: str) -> bool:
        """Return whether a key is present.

        Args:
            bucket: source bucket.
            key: object key.

        Returns:
            ``True`` when the object exists.

        Raises:
            SourceUnavailableError: if MinIO is unreachable.
        """
        try:
            self.stat(bucket, key)
        except EntityNotFoundError:
            return False
        return True

    def list_objects(self, bucket: str, prefix: str = "") -> Iterator[StoredObject]:
        """Enumerate objects under a prefix.

        Args:
            bucket: source bucket.
            prefix: key prefix to filter by.

        Yields:
            Object metadata, lazily paged. Each one costs a metadata request,
            because a listing does not return the content hash.
        """
        with _translated(bucket):
            listing = self._client.list_objects(bucket, prefix=prefix, recursive=True)
            names = [str(found.object_name) for found in listing if not found.is_dir]
        for name in names:
            yield self.stat(bucket, name)

    def presigned_url(self, bucket: str, key: str, expires_in: timedelta) -> str:
        """Generate a time-limited download URL.

        Args:
            bucket: source bucket.
            key: object key.
            expires_in: how long the URL stays valid.

        Returns:
            A signed URL.

        Raises:
            EntityNotFoundError: if the key does not exist.
        """
        self.stat(bucket, key)
        with _translated(bucket, key):
            url: str = self._client.presigned_get_object(bucket, key, expires=expires_in)
        return url


def build_object_key(
    plant_code: str,
    source_key: str,
    content_hash: str,
    extension: str,
) -> str:
    """Build the canonical object key for a payload archived today.

    The key encodes the provenance, so a duplicate upload collides by
    construction rather than needing a separate lookup.

    Args:
        plant_code: the plant the payload belongs to.
        source_key: the configured source it came from.
        content_hash: SHA-256 hex digest of the payload.
        extension: file extension without the leading dot.

    Returns:
        A key of the form
        ``{plant_code}/{source_key}/{YYYY}/{MM}/{DD}/{content_hash}.{extension}``,
        dated with the current UTC day.
    """
    return archive_object_key(
        plant_code, source_key, content_hash, extension, datetime.now(UTC).date()
    )


__all__ = ["MinioObjectStorage", "build_object_key"]
