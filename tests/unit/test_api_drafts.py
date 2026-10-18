"""The drafts endpoints, through the API (``energy.api.routers.drafts``).

The draft service is the real one, bound to the in-memory repository of
``tests/fixtures/drafts.py`` and to a directory archive, so a request goes
through the router, the service, the guard, the reader and the rules. Who may
call is tested here and in ``test_api_auth.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote
from uuid import uuid4

import pytest
from tests.fixtures.drafts import InMemoryDraftRepository
from tests.fixtures.workbooks import lines_broken, lines_good

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.adapters.local_storage import LocalObjectStorage
from energy.adapters.zip_package import ZipPackageInspector
from energy.domain.drafts import XLSX_MEDIA_TYPE
from energy.ports.draft_repository import NewDraft
from energy.services.draft_service import DraftService

# Nothing of `energy.api` is imported at the top of this module: importing it
# builds the app and freezes the settings before a test can set them.

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

UPLOAD = "/drafts?dataset_key=lineas_produccion"


@pytest.fixture
def repository() -> InMemoryDraftRepository:
    return InMemoryDraftRepository()


@pytest.fixture
def bind_service(
    app: FastAPI, repository: InMemoryDraftRepository, tmp_path: Path
) -> Iterator[Any]:
    """Return a function that binds a draft service, with limits of the test's choice."""
    from energy.api.deps import get_draft_service  # noqa: PLC0415

    def bind(**limits: int) -> DraftService:
        service = DraftService(
            repository=repository,
            storage=LocalObjectStorage(tmp_path / "archive"),
            reader=CalamineWorkbookReader(),
            inspector=ZipPackageInspector(),
            catalogue=app.state.dataset_catalogue,
            bucket="bronze-raw",
            temp_dir=tmp_path,
            **limits,  # type: ignore[arg-type]
        )
        app.dependency_overrides[get_draft_service] = lambda: service
        return service

    bind()
    yield bind
    app.dependency_overrides.pop(get_draft_service, None)


@pytest.fixture
def headers(auth_headers: Any, bind_service: Any) -> dict[str, str]:
    return {
        **auth_headers("operator"),
        "Content-Type": XLSX_MEDIA_TYPE,
        "X-File-Name": quote("Líneas de producción.xlsx"),
    }


def post(client: TestClient, data: bytes, headers: dict[str, str], path: str = UPLOAD) -> Any:
    return client.post(path, content=data, headers=headers)


def test_an_upload_answers_201_with_the_draft(client: TestClient, headers: dict[str, str]) -> None:
    data = lines_good()

    response = post(client, data, headers)

    assert response.status_code == 201
    draft = response.json()
    assert response.headers["Location"].endswith(f"/drafts/{draft['draft_id']}")
    assert {name: draft[name] for name in ("dataset_key", "status", "filename", "size_bytes")} == {
        "dataset_key": "lineas_produccion",
        "status": "uploaded",
        "filename": "Líneas de producción.xlsx",
        "size_bytes": len(data),
    }
    assert draft["validation"] is None
    assert "object_key" not in draft
    assert "bucket" not in draft


def test_the_same_file_again_answers_200_with_the_earlier_draft(
    client: TestClient, headers: dict[str, str]
) -> None:
    first = post(client, lines_good(), headers).json()

    again = post(client, lines_good(), headers)

    assert again.status_code == 200
    assert again.json()["draft_id"] == first["draft_id"]


@pytest.mark.parametrize(
    ("path", "changes", "data", "status", "code"),
    [
        ("/drafts?dataset_key=nada", {}, b"PK\x03\x04", 404, "unknown_dataset"),
        (UPLOAD, {"X-File-Name": "libro.xls"}, b"PK\x03\x04", 415, "unsupported_file_format"),
        (UPLOAD, {"Content-Type": "text/plain"}, b"PK\x03\x04", 415, "unsupported_file_format"),
        (UPLOAD, {}, b"", 415, "empty_file"),
        (UPLOAD, {"X-File-Name": ""}, b"PK\x03\x04", 400, "invalid_filename"),
        (UPLOAD, {"X-File-Name": "%FF.xlsx"}, b"PK\x03\x04", 400, "invalid_filename"),
        (UPLOAD, {}, b"\xd0\xcf\x11\xe0 an old Excel file", 415, "unsupported_file_format"),
    ],
)
def test_an_upload_is_refused_with_a_status_and_a_code(
    client: TestClient,
    headers: dict[str, str],
    path: str,
    changes: dict[str, str],
    data: bytes,
    status: int,
    code: str,
) -> None:
    response = post(client, data, {**headers, **changes}, path)

    assert response.status_code == status
    assert response.json()["code"] == code


def test_an_upload_without_a_size_answers_411(client: TestClient, headers: dict[str, str]) -> None:
    def chunks() -> Iterator[bytes]:
        yield b"PK\x03\x04"
        yield b"more"

    response = client.post(UPLOAD, content=chunks(), headers=headers)

    assert response.status_code == 411
    assert response.json()["code"] == "length_required"


def test_a_file_over_the_limit_answers_413_before_it_is_read(
    client: TestClient, headers: dict[str, str], bind_service: Any, repository: Any
) -> None:
    bind_service(max_file_bytes=1000)

    response = post(client, b"PK\x03\x04" + bytes(2000), headers)

    assert response.status_code == 413
    assert response.json()["code"] == "file_too_large"
    assert repository.drafts == {}


def test_a_draft_is_read_back_by_its_owner(client: TestClient, headers: dict[str, str]) -> None:
    created = post(client, lines_good(), headers).json()

    response = client.get(f"/drafts/{created['draft_id']}", headers=headers)

    assert response.status_code == 200
    assert response.json() == created


def test_a_draft_of_someone_else_answers_404(
    client: TestClient, headers: dict[str, str], repository: InMemoryDraftRepository
) -> None:
    theirs = repository.create(
        NewDraft(
            "lineas_produccion", "manual_sheets", "1515", "a.xlsx", XLSX_MEDIA_TYPE, 9, "x", None
        )
    )

    for path in (f"/drafts/{theirs.draft_id}", f"/drafts/{theirs.draft_id}/issues"):
        response = client.get(path, headers=headers)
        assert response.status_code == 404
        assert response.json()["code"] == "unknown_draft"
    assert client.get(f"/drafts/{uuid4()}", headers=headers).status_code == 404


def test_validating_a_broken_file_lists_every_issue(
    client: TestClient, headers: dict[str, str]
) -> None:
    data, manifest = lines_broken()
    draft = post(client, data, headers).json()

    response = client.post(f"/drafts/{draft['draft_id']}/validate", headers=headers)

    assert response.status_code == 200
    validation = response.json()["validation"]
    assert {name: validation[name] for name in ("rows_read", "rows_with_errors", "blocking")} == {
        "rows_read": 9,
        "rows_with_errors": 7,
        "blocking": True,
    }
    assert {
        "rule": "cell.pattern",
        "severity": "error",
        "column": "Almac",
        "rows": 1,
        "listed": 1,
    } in (validation["counts"])
    issues = client.get(f"/drafts/{draft['draft_id']}/issues", headers=headers).json()
    assert issues["total"] == len(manifest)
    assert {(item["row"], item["column"], item["rule"]) for item in issues["items"]} == set(
        manifest
    )
    allowed = next(item for item in issues["items"] if item["rule"] == "cell.allowed")
    assert allowed == {
        "row": 9,
        "column": "I",
        "column_key": "Firma Automática",
        "rule": "cell.allowed",
        "severity": "error",
        "value": "Tal vez",
        "params": {"allowed": ["Sí", "No"]},
    }


def test_issues_are_filtered_and_paged(client: TestClient, headers: dict[str, str]) -> None:
    data, _manifest = lines_broken()
    draft_id = post(client, data, headers).json()["draft_id"]
    client.post(f"/drafts/{draft_id}/validate", headers=headers)

    page = client.get(
        f"/drafts/{draft_id}/issues?severity=error&rule=cell.pattern&offset=1&limit=1",
        headers=headers,
    ).json()

    assert (page["total"], page["offset"], page["limit"]) == (3, 1, 1)
    assert [item["row"] for item in page["items"]] == [5]
    assert (
        client.get(f"/drafts/{draft_id}/issues?severity=fatal", headers=headers).status_code == 422
    )
    assert client.get(f"/drafts/{draft_id}/issues?limit=501", headers=headers).status_code == 422


def test_a_file_over_the_threshold_is_archived_and_answers_501(
    client: TestClient, headers: dict[str, str], bind_service: Any
) -> None:
    bind_service(check_max_bytes=1000)
    draft = post(client, lines_good(), headers).json()

    response = client.post(f"/drafts/{draft['draft_id']}/validate", headers=headers)

    assert draft["status"] == "uploaded"
    assert response.status_code == 501
    assert response.json()["code"] == "not_implemented"
    assert "Increment 12" in response.json()["message"]


def test_a_draft_still_arriving_answers_409(
    client: TestClient, headers: dict[str, str], repository: InMemoryDraftRepository
) -> None:
    subject = "00000000-0000-4000-8000-00000000abcd"
    arriving = repository.create(
        NewDraft(
            "lineas_produccion",
            "manual_sheets",
            "1515",
            "a.xlsx",
            XLSX_MEDIA_TYPE,
            9,
            subject,
            None,
        )
    )

    response = client.post(f"/drafts/{arriving.draft_id}/validate", headers=headers)

    assert response.status_code == 409
    assert response.json()["code"] == "draft_busy"


def test_the_datasets_tell_the_browser_how_large_a_file_may_be(
    client: TestClient, headers: dict[str, str]
) -> None:
    limits = client.get("/datasets", headers=headers).json()["limits"]

    assert limits == {"max_file_bytes": 314_572_800, "validated_in_request_bytes": 5_242_880}


async def test_the_request_body_never_returns_more_than_was_asked() -> None:
    """minio-py loops ``while size:``; a longer answer would read the whole file into one part."""
    import anyio.to_thread  # noqa: PLC0415

    from energy.api.routers.drafts import _RequestBody  # noqa: PLC0415

    chunks = [bytes([n]) * 65_536 for n in range(5)]

    class Request:
        async def _chunks(self) -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk

        def stream(self) -> AsyncIterator[bytes]:
            return self._chunks()

    body = _RequestBody(Request())  # type: ignore[arg-type]

    def drain() -> list[bytes]:
        answers = []
        for size in (1, 100_000, 65_535, 7, -1, 3, 10, 10):
            answers.append(body.read(size))
        return answers

    answers = await anyio.to_thread.run_sync(drain)

    asked = (1, 100_000, 65_535, 7, -1, 3, 10, 10)
    assert all(size < 0 or len(answer) <= size for size, answer in zip(asked, answers, strict=True))
    rest = []
    while chunk := await anyio.to_thread.run_sync(body.read, 50_000):
        rest.append(chunk)
    assert b"".join(answers + rest) == b"".join(chunks)
