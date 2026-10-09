"""Tests for the directory-backed object storage.

Layer: tests (unit)

The adapter stands in for MinIO wherever MinIO cannot be reached, so it has to
keep the same two promises: an object is written once, and what is read back is
verified against the hash recorded when it was stored.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from energy.adapters.local_storage import LocalObjectStorage
from energy.domain.exceptions import (
    ConfigurationError,
    ContentHashMismatchError,
    EntityNotFoundError,
)
from energy.ports.object_storage import ObjectStorage

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

BUCKET = "bronze-raw"
KEY = "1515/qad_rct_wo/2026/10/07/abc.xlsx"
DATA = b"workbook bytes"


@pytest.fixture
def storage(tmp_path: Path) -> LocalObjectStorage:
    """Return a store rooted in a temporary directory."""
    return LocalObjectStorage(tmp_path / "archive")


def test_it_satisfies_the_object_storage_port(storage: LocalObjectStorage) -> None:
    """The adapter is structurally an ObjectStorage."""
    assert isinstance(storage, ObjectStorage)


def test_put_then_get_round_trips_the_bytes(storage: LocalObjectStorage) -> None:
    """What is stored is what comes back, with its metadata."""
    storage.ensure_bucket(BUCKET)
    before = datetime.now(UTC)

    stored = storage.put(BUCKET, KEY, DATA, "application/x-test", {"plant-code": "1515"})

    assert stored.key == KEY
    assert stored.size_bytes == len(DATA)
    assert stored.content_hash == hashlib.sha256(DATA).hexdigest()
    assert stored.stored_at >= before
    assert storage.exists(BUCKET, KEY)
    assert storage.get(BUCKET, KEY) == DATA

    found = storage.stat(BUCKET, KEY)
    assert found.content_hash == stored.content_hash
    assert found.content_type == "application/x-test"
    assert found.metadata == {"plant-code": "1515"}
    assert found.stored_at == stored.stored_at


def test_the_layout_on_disk_mirrors_bucket_and_key(
    storage: LocalObjectStorage, tmp_path: Path
) -> None:
    """A bucket is a directory and a key a path inside it."""
    storage.put(BUCKET, KEY, DATA, "application/x-test")

    assert (tmp_path / "archive" / BUCKET / KEY).read_bytes() == DATA


def test_put_refuses_to_overwrite(storage: LocalObjectStorage) -> None:
    """A Bronze row points at those bytes; replacing them would rewrite history."""
    storage.put(BUCKET, KEY, DATA, "application/x-test")

    with pytest.raises(FileExistsError):
        storage.put(BUCKET, KEY, b"other bytes", "application/x-test")

    assert storage.get(BUCKET, KEY) == DATA


def test_get_detects_bytes_that_changed_on_disk(
    storage: LocalObjectStorage, tmp_path: Path
) -> None:
    """A tampered or corrupted object is reported, not returned."""
    storage.put(BUCKET, KEY, DATA, "application/x-test")
    (tmp_path / "archive" / BUCKET / KEY).write_bytes(b"tampered")

    with pytest.raises(ContentHashMismatchError):
        storage.get(BUCKET, KEY)


def test_missing_objects_are_reported(storage: LocalObjectStorage) -> None:
    """Every read of an absent key says so in the same way."""
    assert not storage.exists(BUCKET, KEY)

    with pytest.raises(EntityNotFoundError):
        storage.get(BUCKET, KEY)
    with pytest.raises(EntityNotFoundError):
        storage.stat(BUCKET, KEY)
    with pytest.raises(EntityNotFoundError):
        storage.open_stream(BUCKET, KEY)
    with pytest.raises(EntityNotFoundError):
        storage.presigned_url(BUCKET, KEY, timedelta(minutes=5))


def test_open_stream_reads_the_payload(storage: LocalObjectStorage) -> None:
    """A stream gives the same bytes as a full read."""
    storage.put(BUCKET, KEY, DATA, "application/x-test")

    with storage.open_stream(BUCKET, KEY) as stream:
        assert stream.read() == DATA


def test_list_objects_filters_by_prefix_and_hides_its_own_files(
    storage: LocalObjectStorage,
) -> None:
    """Listing shows the objects, not the metadata files kept beside them."""
    storage.put(BUCKET, "1515/qad_rct_wo/a.xlsx", b"a", "application/x-test")
    storage.put(BUCKET, "1515/qad_facturas/b.xlsx", b"b", "application/x-test")

    assert [found.key for found in storage.list_objects(BUCKET)] == [
        "1515/qad_facturas/b.xlsx",
        "1515/qad_rct_wo/a.xlsx",
    ]
    assert [found.key for found in storage.list_objects(BUCKET, "1515/qad_rct_wo/")] == [
        "1515/qad_rct_wo/a.xlsx"
    ]
    assert list(storage.list_objects("empty-bucket")) == []


def test_presigned_url_points_at_the_file(storage: LocalObjectStorage) -> None:
    """A directory cannot sign a link, so it hands back a file URL."""
    storage.put(BUCKET, KEY, DATA, "application/x-test")

    url = storage.presigned_url(BUCKET, KEY, timedelta(minutes=5))

    assert url.startswith("file://")
    assert url.endswith("abc.xlsx")


@pytest.mark.parametrize("key", ["../outside.xlsx", "/absolute.xlsx", "a/../../b.xlsx", ""])
def test_keys_that_escape_the_bucket_are_refused(storage: LocalObjectStorage, key: str) -> None:
    """An object key can never reach outside its bucket directory."""
    with pytest.raises(ConfigurationError):
        storage.put(BUCKET, key, DATA, "application/x-test")


@pytest.mark.parametrize("bucket", ["", "..", "a/b", "a\\b"])
def test_bucket_names_that_escape_the_root_are_refused(
    storage: LocalObjectStorage, bucket: str
) -> None:
    """A bucket is one directory directly under the root."""
    with pytest.raises(ConfigurationError):
        storage.ensure_bucket(bucket)
