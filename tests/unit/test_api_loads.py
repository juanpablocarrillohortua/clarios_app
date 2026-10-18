"""Confirming a draft and reading the history, through the API.

The draft and load services are the real ones, bound to the in-memory drafts
of ``tests/fixtures/drafts.py``, the in-memory Bronze of
``tests/fixtures/loads.py`` and a directory archive. Who may call is tested
here and in ``test_api_auth.py``.
"""

from __future__ import annotations

import hashlib
import io
from datetime import date
from typing import TYPE_CHECKING, Any
from urllib.parse import quote
from uuid import UUID, uuid4

import pytest
from tests.fixtures.drafts import InMemoryDraftRepository
from tests.fixtures.loads import InMemoryBronze, InMemoryLoadCommitter, build_load_service
from tests.fixtures.workbooks import (
    LINES_HEADERS,
    LINES_SHEET,
    NBSP,
    build_workbook,
    invoices_good,
    lines_broken,
    lines_catalogue,
    lines_catalogue_changed,
    lines_good,
)

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.adapters.local_storage import LocalObjectStorage
from energy.adapters.zip_package import ZipPackageInspector
from energy.domain.drafts import XLSX_MEDIA_TYPE
from energy.domain.exceptions import ContentHashMismatchError
from energy.domain.landing import landing_rows
from energy.ports.load_ledger import ArchivedOriginal
from energy.services.draft_service import DraftService
from energy.services.load_service import target_of

# Nothing of `energy.api` is imported at the top of this module: importing it
# builds the app and freezes the settings before a test can set them.

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


@pytest.fixture
def bronze() -> InMemoryBronze:
    return InMemoryBronze()


@pytest.fixture
def committer(
    app: FastAPI, bronze: InMemoryBronze, tmp_path: Path
) -> Iterator[InMemoryLoadCommitter]:
    """Bind the draft and load services to in-memory drafts and Bronze; return the committer."""
    from energy.api.deps import get_draft_service, get_load_service  # noqa: PLC0415

    repository = InMemoryDraftRepository()
    storage = LocalObjectStorage(tmp_path / "archive")
    catalogue = app.state.dataset_catalogue
    drafts = DraftService(
        repository=repository,
        storage=storage,
        reader=CalamineWorkbookReader(),
        inspector=ZipPackageInspector(),
        catalogue=catalogue,
        bucket="bronze-raw",
        temp_dir=tmp_path,
    )
    loads, commit, _ledger = build_load_service(drafts, repository, storage, catalogue, bronze)
    app.dependency_overrides[get_draft_service] = lambda: drafts
    app.dependency_overrides[get_load_service] = lambda: loads
    yield commit
    app.dependency_overrides.pop(get_draft_service, None)
    app.dependency_overrides.pop(get_load_service, None)


@pytest.fixture
def operator(auth_headers: Any, committer: InMemoryLoadCommitter) -> dict[str, str]:
    return auth_headers("operator")


def validated(
    client: TestClient,
    headers: dict[str, str],
    data: bytes,
    dataset_key: str = "lineas_produccion",
    name: str = "Líneas de producción.xlsx",
) -> dict[str, Any]:
    """Upload a file and validate it, through the API; return the draft."""
    uploaded = client.post(
        f"/drafts?dataset_key={dataset_key}",
        content=data,
        headers={**headers, "Content-Type": XLSX_MEDIA_TYPE, "X-File-Name": quote(name)},
    ).json()
    if uploaded["status"] == "committed":
        return dict(uploaded)
    return dict(client.post(f"/drafts/{uploaded['draft_id']}/validate", headers=headers).json())


def commit(
    client: TestClient, headers: dict[str, str], draft: dict[str, Any], accept: bool = False
) -> Any:
    """Commit a draft as its confirmation shows it."""
    shown = client.get(f"/drafts/{draft['draft_id']}/confirmation", headers=headers).json()
    current = shown["current_version"]
    return client.post(
        f"/drafts/{draft['draft_id']}/commit",
        json={
            "validation_digest": shown["validation_digest"],
            "accept_warnings": accept,
            "base_run_id": current["run_id"] if current else None,
        },
        headers=headers,
    )


def lines_with_a_warning() -> bytes:
    row = ["FAFORM01", "1010", "Formación", 100, "", f"Manual{NBSP}", "", "1 02:30:00", "Sí", ""]
    return build_workbook({LINES_SHEET: [LINES_HEADERS, row]})


def catalogue_payloads() -> list[dict[str, str]]:
    import json  # noqa: PLC0415

    sheet = CalamineWorkbookReader().open_workbook(lines_catalogue()).sheet(LINES_SHEET)
    return [json.loads(row.payload_json) for row in landing_rows(sheet.rows)]


# =============================================================================
# The confirmation and the difference
# =============================================================================
def test_the_confirmation_of_a_movement(
    client: TestClient, operator: dict[str, str], app: FastAPI
) -> None:
    data, expected = invoices_good(app.state.dataset_catalogue.get("qad_facturas"))
    draft = validated(client, operator, data, "qad_facturas", "Facturas.xlsx")

    response = client.get(f"/drafts/{draft['draft_id']}/confirmation", headers=operator)

    assert response.status_code == 200
    shown = response.json()
    assert {
        name: shown[name] for name in ("kind", "sheet", "can_confirm", "blocked_by", "rows")
    } == {
        "kind": "movement",
        "sheet": "Datos",
        "can_confirm": True,
        "blocked_by": None,
        "rows": expected["rows"],
    }
    assert shown["period"] == {"start": expected["window_start"], "end": expected["window_end"]}
    assert len(shown["validation_digest"]) == 64
    assert (shown["warnings"], shown["warnings_to_accept"]) == ([], False)
    assert (shown["current_version"], shown["difference"], shown["run_id"]) == (None, None, None)


def test_the_confirmation_of_a_catalogue_and_its_difference(
    client: TestClient, operator: dict[str, str], app: FastAPI, bronze: InMemoryBronze
) -> None:
    lines = target_of(app.state.dataset_catalogue.get("lineas_produccion"))
    current = bronze.add_cli_load(lines, catalogue_payloads())
    data, expected = lines_catalogue_changed()
    draft = validated(client, operator, data)

    shown = client.get(f"/drafts/{draft['draft_id']}/confirmation", headers=operator).json()
    removed = client.get(
        f"/drafts/{draft['draft_id']}/difference?change=removed", headers=operator
    ).json()
    changed = client.get(
        f"/drafts/{draft['draft_id']}/difference?change=changed", headers=operator
    ).json()

    assert shown["current_version"]["run_id"] == str(current)
    assert shown["current_version"]["origin"] == "cli"
    assert shown["difference"] == {
        "base_run_id": str(current),
        **expected,
        "removed_rows": "kept",
    }
    assert removed["total"] == 1
    assert removed["items"][0]["key"] == ["MONT04"]
    assert removed["items"][0]["row"] is None
    assert removed["items"][0]["values"]["Descripción"] == "Montaje 4"
    assert changed["items"][0]["columns"] == [
        {"column": "Descripción", "before": "Formación 1", "after": "Formación uno"}
    ]


def test_the_difference_needs_a_kind_of_change(
    client: TestClient, operator: dict[str, str]
) -> None:
    draft = validated(client, operator, lines_good())

    assert (
        client.get(f"/drafts/{draft['draft_id']}/difference", headers=operator).status_code == 422
    )
    assert (
        client.get(
            f"/drafts/{draft['draft_id']}/difference?change=moved", headers=operator
        ).status_code
        == 422
    )


def test_a_draft_with_warnings_shows_them_to_accept(
    client: TestClient, operator: dict[str, str]
) -> None:
    draft = validated(client, operator, lines_with_a_warning())

    shown = client.get(f"/drafts/{draft['draft_id']}/confirmation", headers=operator).json()

    assert shown["warnings_to_accept"]
    (warning,) = shown["warnings"]
    assert (warning["rule"], warning["rows"]) == ("cell.text_damaged", 1)
    assert warning["reasons"] == [{"reason": "text.invisible_characters", "rows": 1}]


# =============================================================================
# The commit
# =============================================================================
def test_a_commit_answers_201_with_the_load_and_its_address(
    client: TestClient, operator: dict[str, str]
) -> None:
    draft = validated(client, operator, lines_good())

    response = commit(client, operator, draft)

    assert response.status_code == 201
    load = response.json()
    assert response.headers["Location"].endswith(f"/loads/{load['run_id']}")
    assert {name: load[name] for name in ("origin", "state", "rows", "dataset_key", "mine")} == {
        "origin": "app",
        "state": "completed",
        "rows": 2,
        "dataset_key": "lineas_produccion",
        "mine": True,
    }
    assert load["draft_id"] == draft["draft_id"]
    assert load["sheets"] == [
        {"name": "Lineas Produccion", "dataset_key": "lineas_produccion", "rows": 2}
    ]
    assert load["original"]["filename"] == "Líneas de producción.xlsx"
    assert "bucket" not in load["original"]
    assert "object_key" not in load["original"]
    assert load["validation"]["rows_read"] == 2
    assert load["difference"]["added"] == 2
    after = client.get(f"/drafts/{draft['draft_id']}", headers=operator).json()
    assert (after["status"], after["run_id"]) == ("committed", load["run_id"])


def test_a_second_commit_answers_200_with_the_same_load(
    client: TestClient, operator: dict[str, str], bronze: InMemoryBronze
) -> None:
    draft = validated(client, operator, lines_good())
    first = commit(client, operator, draft).json()

    again = client.post(
        f"/drafts/{draft['draft_id']}/commit",
        json={"validation_digest": "0" * 64},
        headers=operator,
    )

    assert again.status_code == 200
    assert again.json()["run_id"] == first["run_id"]
    assert "Location" not in again.headers
    assert len(bronze.runs) == 1


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({}, "validation_digest"),
        ({"validation_digest": "not a digest"}, "validation_digest"),
        ({"validation_digest": "0" * 64, "who": "someone"}, "who"),
    ],
)
def test_a_commit_body_that_is_not_one_answers_422(
    client: TestClient, operator: dict[str, str], body: dict[str, Any], detail: str
) -> None:
    draft = validated(client, operator, lines_good())

    response = client.post(f"/drafts/{draft['draft_id']}/commit", json=body, headers=operator)

    assert response.status_code == 422
    assert detail in str(response.json())


def test_each_refusal_has_its_status_and_code(
    client: TestClient,
    operator: dict[str, str],
    committer: InMemoryLoadCommitter,
    app: FastAPI,
) -> None:
    good = validated(client, operator, lines_good())
    broken = validated(client, operator, lines_broken()[0])
    warned = validated(client, operator, lines_with_a_warning())

    def status_of(draft: dict[str, Any], headers: dict[str, str] = operator, **body: Any) -> Any:
        response = client.post(
            f"/drafts/{draft['draft_id']}/commit",
            json={"validation_digest": "0" * 64, **body},
            headers=headers,
        )
        return response.status_code, response.json().get("code")

    assert status_of(good) == (409, "validation_changed")
    assert status_of(broken) == (422, "draft_blocked")
    assert commit(client, operator, warned).status_code == 422
    assert commit(client, operator, warned).json()["code"] == "warnings_not_accepted"
    assert status_of({"draft_id": str(uuid4())}) == (404, "unknown_draft")
    committer.held_drafts.add(UUID(good["draft_id"]))
    assert commit(client, operator, good).status_code == 409
    assert commit(client, operator, good).json()["code"] == "draft_busy"
    committer.held_drafts.clear()
    committer.held_datasets.add(target_of(app.state.dataset_catalogue.get("lineas_produccion")))
    assert commit(client, operator, good).json()["code"] == "dataset_busy"


def test_a_file_the_command_line_already_loaded_is_refused(
    client: TestClient, operator: dict[str, str], app: FastAPI, bronze: InMemoryBronze
) -> None:
    data = lines_good()
    loaded = bronze.add_cli_load(
        target_of(app.state.dataset_catalogue.get("lineas_produccion")),
        [{"Línea": "FAFORM01"}],
        content_hash=hashlib.sha256(data).hexdigest(),
    )
    draft = validated(client, operator, data)

    shown = client.get(f"/drafts/{draft['draft_id']}/confirmation", headers=operator).json()
    response = commit(client, operator, draft)

    assert (shown["can_confirm"], shown["blocked_by"]) == (False, "already_loaded")
    assert shown["already_loaded_run_id"] == str(loaded)
    assert (response.status_code, response.json()["code"]) == (409, "already_loaded")
    assert response.json()["details"]["run_id"] == str(loaded)


def test_a_catalogue_whose_current_version_moved_answers_409(
    client: TestClient, operator: dict[str, str], app: FastAPI, bronze: InMemoryBronze
) -> None:
    draft = validated(client, operator, lines_catalogue_changed()[0])
    shown = client.get(f"/drafts/{draft['draft_id']}/confirmation", headers=operator).json()
    bronze.add_cli_load(
        target_of(app.state.dataset_catalogue.get("lineas_produccion")), catalogue_payloads()
    )

    response = client.post(
        f"/drafts/{draft['draft_id']}/commit",
        json={"validation_digest": shown["validation_digest"], "base_run_id": None},
        headers=operator,
    )

    assert (response.status_code, response.json()["code"]) == (409, "current_version_changed")


def test_warnings_accepted_land_and_are_recorded(
    client: TestClient, operator: dict[str, str]
) -> None:
    draft = validated(client, operator, lines_with_a_warning())

    load = commit(client, operator, draft, accept=True).json()

    assert load["warnings_accepted"] == 1
    assert load["validation"]["accepted"] == [
        {
            "rule": "cell.text_damaged",
            "severity": "warning",
            "column": "Método de cantidad de transacción",
            "rows": 1,
        }
    ]


# =============================================================================
# History and detail
# =============================================================================
def test_the_history_lists_every_load_with_its_filters(
    client: TestClient, operator: dict[str, str], app: FastAPI, bronze: InMemoryBronze
) -> None:
    invoices = target_of(app.state.dataset_catalogue.get("qad_facturas"))
    cli = bronze.add_cli_load(
        invoices, [{"Núm Fact": "A"}], window=(date(2025, 9, 1), date(2025, 9, 30))
    )
    mine = commit(client, operator, validated(client, operator, lines_good())).json()["run_id"]

    everything = client.get("/loads", headers=operator).json()
    by_me = client.get("/loads?user=me", headers=operator).json()
    from_cli = client.get("/loads?origin=cli", headers=operator).json()
    of_lines = client.get("/loads?dataset=lineas_produccion", headers=operator).json()
    september = client.get(
        "/loads?period_from=2025-09-15&period_to=2025-09-20", headers=operator
    ).json()

    assert everything["total"] == 2
    assert {item["run_id"] for item in everything["items"]} == {str(cli), mine}
    assert [item["run_id"] for item in by_me["items"]] == [mine]
    assert [item["run_id"] for item in from_cli["items"]] == [str(cli)]
    assert from_cli["items"][0]["uploaded_by_name"] is None
    assert from_cli["items"][0]["mine"] is False
    assert [item["run_id"] for item in of_lines["items"]] == [mine]
    assert [item["run_id"] for item in september["items"]] == [str(cli)]
    assert everything["people"] == [
        {"subject": everything["people"][0]["subject"], "name": "Ada Lovelace"}
    ]


@pytest.mark.parametrize(
    "query",
    [
        "state=done",
        "sort=name",
        "order=up",
        "limit=0",
        "limit=101",
        "offset=-1",
        "from=yesterday",
        "origin=excel",
    ],
)
def test_a_history_query_that_is_not_one_answers_422(
    client: TestClient, operator: dict[str, str], query: str
) -> None:
    assert client.get(f"/loads?{query}", headers=operator).status_code == 422


def test_a_history_of_an_unknown_dataset_answers_404(
    client: TestClient, operator: dict[str, str]
) -> None:
    response = client.get("/loads?dataset=nada", headers=operator)

    assert (response.status_code, response.json()["code"]) == (404, "unknown_dataset")


def test_a_load_in_detail_names_people_by_display_name_only(
    client: TestClient, operator: dict[str, str]
) -> None:
    run_id = commit(client, operator, validated(client, operator, lines_good())).json()["run_id"]

    response = client.get(f"/loads/{run_id}", headers=operator)

    load = response.json()
    assert (load["draft_available"], load["mine"]) == (True, True)
    assert load["uploaded_by_name"] == "Ada Lovelace"
    assert "example.test" not in response.text
    assert "uploaded_by" not in load


def test_an_unknown_load_answers_404(client: TestClient, operator: dict[str, str]) -> None:
    response = client.get(f"/loads/{uuid4()}", headers=operator)

    assert (response.status_code, response.json()["code"]) == (404, "unknown_load")


def test_the_original_is_streamed_as_it_was_uploaded(
    client: TestClient, operator: dict[str, str]
) -> None:
    data = lines_good()
    run_id = commit(client, operator, validated(client, operator, data)).json()["run_id"]

    response = client.get(f"/loads/{run_id}/original", headers=operator)

    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == XLSX_MEDIA_TYPE
    assert response.headers["content-length"] == str(len(data))
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == (
        'attachment; filename="L_neas de producci_n.xlsx";'
        " filename*=UTF-8''L%C3%ADneas%20de%20producci%C3%B3n.xlsx"
    )


def test_an_original_that_does_not_match_its_hash_never_arrives_whole() -> None:
    from energy.api.routers.loads import _verified  # noqa: PLC0415

    data = b"x" * (3 * 1024 * 1024 + 10)
    original = ArchivedOriginal("bronze-raw", "k", "a.xlsx", XLSX_MEDIA_TYPE, len(data), "0" * 64)
    sent = bytearray()

    def download() -> None:
        for chunk in _verified(original, io.BufferedReader(io.BytesIO(data))):  # type: ignore[arg-type]
            sent.extend(chunk)

    with pytest.raises(ContentHashMismatchError):
        download()

    assert len(sent) < len(data)
    assert bytes(sent) == data[: len(sent)]
