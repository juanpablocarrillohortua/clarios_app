"""Directory-backed implementation of the object storage port.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.object_storage.ObjectStorage` on a plain
    directory, for the places MinIO cannot be reached: a developer's machine
    running a load from the host, and the test suite. A bucket is a
    subdirectory and an object key is a relative path inside it, so the layout
    on disk mirrors what the same call would produce in MinIO. It keeps the
    port's two guarantees: an object is written once and never overwritten, and
    a payload is verified against its recorded SHA-256 when it is read back.
    Each object has a small JSON sidecar holding its metadata, because a file
    system has nowhere else to put it.

Depends on:
    ``energy.ports.object_storage``, ``energy.domain.exceptions``, standard
    library only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final

from energy.domain.exceptions import (
    ConfigurationError,
    ContentHashMismatchError,
    EntityNotFoundError,
)
from energy.ports.object_storage import StoredObject

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from datetime import timedelta
    from io import BufferedReader

_SIDECAR_SUFFIX: Final = ".metadata.json"
_PARTIAL_SUFFIX: Final = ".part"


class LocalObjectStorage:
    """Object storage kept in a directory.

    Args:
        root: the directory that holds one subdirectory per bucket. It is
            created on first use.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _bucket_path(self, bucket: str) -> Path:
        """Return the directory of a bucket, refusing names that escape the root."""
        if not bucket or bucket in {".", ".."} or "/" in bucket or "\\" in bucket:
            raise ConfigurationError("invalid bucket name", bucket=bucket)
        return self._root / bucket

    def _object_path(self, bucket: str, key: str) -> Path:
        """Return the file an object key maps to, refusing keys that escape the bucket."""
        parts = PurePosixPath(key).parts
        if not parts or PurePosixPath(key).is_absolute() or ".." in parts or "\\" in key:
            raise ConfigurationError("invalid object key", key=key)
        return self._bucket_path(bucket).joinpath(*parts)

    @staticmethod
    def _sidecar(path: Path) -> Path:
        """Return the metadata file that sits beside an object."""
        return path.with_name(path.name + _SIDECAR_SUFFIX)

    def ensure_bucket(self, bucket: str) -> None:
        """Create the bucket directory if it does not exist.

        Args:
            bucket: bucket name.
        """
        self._bucket_path(bucket).mkdir(parents=True, exist_ok=True)

    def put(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StoredObject:
        """Store a payload, refusing to overwrite an existing key.

        The bytes are written to a temporary file and renamed into place, so a
        crash mid-write never leaves a truncated object under the real key.

        Args:
            bucket: target bucket.
            key: object key.
            data: the raw bytes exactly as received.
            content_type: MIME type of the payload.
            metadata: user metadata to keep with the object.

        Returns:
            The :class:`~energy.ports.object_storage.StoredObject` written.

        Raises:
            FileExistsError: if the key is already present.
        """
        path = self._object_path(bucket, key)
        if path.exists():
            raise FileExistsError(f"object already exists: {bucket}/{key}")

        path.parent.mkdir(parents=True, exist_ok=True)
        stored = StoredObject(
            key=key,
            size_bytes=len(data),
            content_hash=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
            stored_at=datetime.now(UTC),
            metadata=dict(metadata or {}),
        )

        partial = path.with_name(path.name + _PARTIAL_SUFFIX)
        partial.write_bytes(data)
        self._sidecar(path).write_text(
            json.dumps(
                {
                    "content_hash": stored.content_hash,
                    "content_type": stored.content_type,
                    "size_bytes": stored.size_bytes,
                    "stored_at": stored.stored_at.isoformat(),
                    "metadata": dict(stored.metadata),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        partial.replace(path)
        return stored

    def get(self, bucket: str, key: str) -> bytes:
        """Retrieve a payload and verify its integrity.

        Args:
            bucket: source bucket.
            key: object key.

        Returns:
            The stored bytes.

        Raises:
            EntityNotFoundError: if the key does not exist.
            ContentHashMismatchError: if the bytes do not hash to the digest
                recorded when the object was stored.
        """
        recorded = self.stat(bucket, key)
        data = self._object_path(bucket, key).read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != recorded.content_hash:
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
        path = self._object_path(bucket, key)
        if not path.is_file():
            raise EntityNotFoundError("object not found", bucket=bucket, key=key)
        return path.open("rb")

    def stat(self, bucket: str, key: str) -> StoredObject:
        """Return an object's metadata without reading its payload.

        Args:
            bucket: source bucket.
            key: object key.

        Returns:
            The object metadata.

        Raises:
            EntityNotFoundError: if the key does not exist.
        """
        path = self._object_path(bucket, key)
        sidecar = self._sidecar(path)
        if not path.is_file() or not sidecar.is_file():
            raise EntityNotFoundError("object not found", bucket=bucket, key=key)
        recorded = json.loads(sidecar.read_text(encoding="utf-8"))
        return StoredObject(
            key=key,
            size_bytes=int(recorded["size_bytes"]),
            content_hash=str(recorded["content_hash"]),
            content_type=str(recorded["content_type"]),
            stored_at=datetime.fromisoformat(recorded["stored_at"]),
            metadata=dict(recorded.get("metadata", {})),
        )

    def exists(self, bucket: str, key: str) -> bool:
        """Return whether a key is present.

        Args:
            bucket: source bucket.
            key: object key.

        Returns:
            ``True`` when the object and its metadata are both there.
        """
        path = self._object_path(bucket, key)
        return path.is_file() and self._sidecar(path).is_file()

    def list_objects(self, bucket: str, prefix: str = "") -> Iterator[StoredObject]:
        """Enumerate objects under a prefix.

        Args:
            bucket: source bucket.
            prefix: key prefix to filter by.

        Yields:
            Object metadata, in key order.
        """
        bucket_path = self._bucket_path(bucket)
        if not bucket_path.is_dir():
            return
        for path in sorted(bucket_path.rglob("*")):
            if not path.is_file() or path.name.endswith((_SIDECAR_SUFFIX, _PARTIAL_SUFFIX)):
                continue
            key = path.relative_to(bucket_path).as_posix()
            if key.startswith(prefix) and self.exists(bucket, key):
                yield self.stat(bucket, key)

    def presigned_url(self, bucket: str, key: str, expires_in: timedelta) -> str:
        """Return a ``file://`` URL for the object.

        A directory has no way to sign or expire a link, so ``expires_in`` is
        accepted for compatibility with the port and otherwise ignored.

        Args:
            bucket: source bucket.
            key: object key.
            expires_in: ignored.

        Returns:
            The file URL.

        Raises:
            EntityNotFoundError: if the key does not exist.
        """
        del expires_in
        if not self.exists(bucket, key):
            raise EntityNotFoundError("object not found", bucket=bucket, key=key)
        return self._object_path(bucket, key).resolve().as_uri()


__all__ = ["LocalObjectStorage"]
