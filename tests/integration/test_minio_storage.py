"""Verify the MinIO adapter against a real MinIO server.

Layer: tests (integration)

Object storage is where the original workbooks are kept, and a Bronze row's
``source_uri`` is only worth something if the bytes it points at are still
there and still the same. These tests run the adapter against a MinIO
container and are skipped when Docker cannot start one.
"""

from __future__ import annotations

import contextlib
import hashlib
from datetime import timedelta
from typing import TYPE_CHECKING

import httpx
import pytest

from energy.adapters.minio_storage import MinioObjectStorage, build_object_key
from energy.core.settings import MinioSettings
from energy.domain.exceptions import EntityNotFoundError, SourceUnavailableError
from energy.ports.object_storage import ObjectStorage

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration

# The release docker-compose.yml pins, pulled from MinIO's own registry: the
# Docker Hub mirror refuses anonymous pulls.
MINIO_IMAGE = "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z"

BUCKET = "bronze-raw-test"
DATA = b"the bytes of a workbook"
CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(scope="module")
def storage() -> Iterator[MinioObjectStorage]:
    """Return the adapter bound to a MinIO container, or skip."""
    try:
        from testcontainers.minio import MinioContainer  # noqa: PLC0415
    except ImportError:  # pragma: no cover - testcontainers is a dev dependency
        pytest.skip("testcontainers is not installed")

    container = None
    try:
        container = MinioContainer(image=MINIO_IMAGE)
        container.start()
    except Exception as exc:  # any Docker failure means "skip"
        # A container that started but did not answer in time is still running.
        if container is not None:
            with contextlib.suppress(Exception):
                container.stop()
        pytest.skip(f"cannot start a MinIO test container: {exc}")

    try:
        config = container.get_config()
        settings = MinioSettings(
            _env_file=None,
            MINIO_ENDPOINT=config["endpoint"],
            MINIO_ROOT_USER=config["access_key"],
            MINIO_ROOT_PASSWORD=config["secret_key"],
            MINIO_SECURE=False,
        )
        adapter = MinioObjectStorage(settings)
        adapter.ensure_bucket(BUCKET)
        yield adapter
    finally:
        container.stop()


def test_it_satisfies_the_object_storage_port(storage: MinioObjectStorage) -> None:
    """The adapter is structurally an ObjectStorage."""
    assert isinstance(storage, ObjectStorage)


def test_ensure_bucket_is_idempotent(storage: MinioObjectStorage) -> None:
    """Creating a bucket that exists is a no-op, so start-up can always call it."""
    storage.ensure_bucket(BUCKET)
    storage.ensure_bucket(BUCKET)


def test_put_then_get_round_trips_the_bytes(storage: MinioObjectStorage) -> None:
    """What is stored is what comes back, with its hash and metadata."""
    key = "round-trip/workbook.xlsx"

    stored = storage.put(BUCKET, key, DATA, CONTENT_TYPE, {"plant-code": "1515"})

    assert stored.content_hash == hashlib.sha256(DATA).hexdigest()
    assert storage.exists(BUCKET, key)
    assert storage.get(BUCKET, key) == DATA

    found = storage.stat(BUCKET, key)
    assert found.size_bytes == len(DATA)
    assert found.content_hash == stored.content_hash
    assert found.content_type == CONTENT_TYPE
    assert found.metadata["plant-code"] == "1515"
    assert found.stored_at.tzinfo is not None


def test_put_refuses_to_overwrite(storage: MinioObjectStorage) -> None:
    """A Bronze row points at those bytes; replacing them would rewrite history."""
    key = "write-once/workbook.xlsx"
    storage.put(BUCKET, key, DATA, CONTENT_TYPE)

    with pytest.raises(FileExistsError):
        storage.put(BUCKET, key, b"other bytes", CONTENT_TYPE)

    assert storage.get(BUCKET, key) == DATA


def test_missing_objects_are_reported(storage: MinioObjectStorage) -> None:
    """Every read of an absent key says so in the same way."""
    key = "missing/nothing.xlsx"

    assert not storage.exists(BUCKET, key)
    with pytest.raises(EntityNotFoundError):
        storage.get(BUCKET, key)
    with pytest.raises(EntityNotFoundError):
        storage.stat(BUCKET, key)
    with pytest.raises(EntityNotFoundError):
        storage.open_stream(BUCKET, key)
    with pytest.raises(EntityNotFoundError):
        storage.presigned_url(BUCKET, key, timedelta(minutes=5))
    with pytest.raises(EntityNotFoundError):
        storage.stat("no-such-bucket", key)


def test_open_stream_reads_the_payload(storage: MinioObjectStorage) -> None:
    """A stream gives the same bytes as a full read."""
    key = "stream/workbook.xlsx"
    storage.put(BUCKET, key, DATA, CONTENT_TYPE)

    with storage.open_stream(BUCKET, key) as stream:
        assert stream.read() == DATA


def test_list_objects_filters_by_prefix(storage: MinioObjectStorage) -> None:
    """A listing walks a prefix and returns each object's metadata."""
    storage.put(BUCKET, "listing/1515/a.xlsx", b"a", CONTENT_TYPE)
    storage.put(BUCKET, "listing/1515/b.xlsx", b"b", CONTENT_TYPE)
    storage.put(BUCKET, "listing/1010/c.xlsx", b"c", CONTENT_TYPE)

    found = list(storage.list_objects(BUCKET, "listing/1515/"))

    assert [item.key for item in found] == ["listing/1515/a.xlsx", "listing/1515/b.xlsx"]
    assert found[0].content_hash == hashlib.sha256(b"a").hexdigest()


def test_presigned_url_downloads_the_object(storage: MinioObjectStorage) -> None:
    """The signed link returns the bytes without any credentials."""
    key = "presigned/workbook.xlsx"
    storage.put(BUCKET, key, DATA, CONTENT_TYPE)

    url = storage.presigned_url(BUCKET, key, timedelta(minutes=5))

    assert httpx.get(url, timeout=30).content == DATA


def test_build_object_key_follows_the_archive_convention() -> None:
    """The key carries plant, source and date, and ends in the content hash."""
    key = build_object_key("1515", "qad_rct_wo", "ab" * 32, "xlsx")

    parts = key.split("/")
    assert parts[:2] == ["1515", "qad_rct_wo"]
    assert len(parts) == 6
    assert parts[-1] == f"{'ab' * 32}.xlsx"


def test_an_unreachable_server_is_reported_as_unavailable() -> None:
    """A wrong endpoint is a clear error, not a hang or a raw socket exception."""
    settings = MinioSettings(
        _env_file=None,
        MINIO_ENDPOINT="127.0.0.1:1",
        MINIO_ROOT_USER="nobody",
        MINIO_ROOT_PASSWORD="nothing-here",
        MINIO_SECURE=False,
    )

    with pytest.raises(SourceUnavailableError):
        MinioObjectStorage(settings).ensure_bucket(BUCKET)
