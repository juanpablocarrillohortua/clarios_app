"""``GET /datasets`` and the template of each dataset, through the API.

The API reads the committed specs when it is built, so these tests see the two
pilots: ``lineas_produccion`` and ``qad_facturas``. Who may call is tested here
and in ``test_api_auth.py``; what a spec may hold, in ``test_dataset_spec.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fixtures.drafts import InMemoryDraftRepository
from tests.fixtures.loads import InMemoryBronze, build_load_service

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.domain.exceptions import DatasetSpecError
from energy.domain.landing import header_keys

# Nothing of `energy.api` is imported at the top of this module: importing it
# builds the app and freezes the settings before a test can set them.

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(autouse=True)
def bronze(app: FastAPI, tmp_path: Path) -> Iterator[InMemoryBronze]:
    """Bind the load service to an in-memory Bronze, which says when each dataset was loaded."""
    from energy.adapters.local_storage import LocalObjectStorage  # noqa: PLC0415
    from energy.adapters.zip_package import ZipPackageInspector  # noqa: PLC0415
    from energy.api.deps import get_load_service  # noqa: PLC0415
    from energy.services.draft_service import DraftService  # noqa: PLC0415

    repository = InMemoryDraftRepository()
    storage = LocalObjectStorage(tmp_path / "archive")
    catalogue = app.state.dataset_catalogue
    drafts = DraftService(
        repository,
        storage,
        CalamineWorkbookReader(),
        ZipPackageInspector(),
        catalogue,
        "bronze-raw",
    )
    shared = InMemoryBronze()
    service, _committer, _ledger = build_load_service(
        drafts, repository, storage, catalogue, shared
    )
    app.dependency_overrides[get_load_service] = lambda: service
    yield shared
    app.dependency_overrides.pop(get_load_service, None)


def test_without_a_token_the_specs_are_not_served(client: TestClient, token_verifier: Any) -> None:
    assert client.get("/datasets").status_code == 401
    assert client.get("/datasets/qad_facturas/template").status_code == 401


@pytest.mark.parametrize("role", ["operator", "engineer", "admin"])
def test_who_loads_data_reads_every_spec(client: TestClient, auth_headers: Any, role: str) -> None:
    response = client.get("/datasets", headers=auth_headers(role))

    assert response.status_code == 200
    assert [dataset["key"] for dataset in response.json()["datasets"]] == [
        "lineas_produccion",
        "qad_facturas",
    ]


def test_a_catalogue_is_described_for_the_web_app(client: TestClient, auth_headers: Any) -> None:
    datasets = client.get("/datasets", headers=auth_headers("operator")).json()["datasets"]
    lines = datasets[0]

    assert {name: lines[name] for name in lines if name != "columns"} == {
        "key": "lineas_produccion",
        "kind": "snapshot",
        "origin": "catalogue",
        "label": "datasets.lineas_produccion.name",
        "description": "datasets.lineas_produccion.description",
        "entry_modes": ["upload"],
        "sheet": "Lineas Produccion",
        "header_row": 1,
        "natural_key": ["Línea"],
        "period_column": None,
        "last_load": None,
    }
    assert lines["columns"][0] == {
        "header": "Línea",
        "key": "Línea",
        "label": None,
        "type": "text",
        "required": True,
        "pattern": "^[A-Z0-9]+$",
        "allowed": None,
        "min": None,
        "max": None,
        "severity": "error",
    }
    assert lines["columns"][8]["allowed"] == ["Sí", "No"]


def test_an_export_carries_every_header_and_its_period(
    client: TestClient, auth_headers: Any
) -> None:
    invoices = client.get("/datasets", headers=auth_headers("operator")).json()["datasets"][1]

    assert (invoices["origin"], invoices["kind"]) == ("export", "movement")
    assert invoices["entry_modes"] == ["upload"]
    assert invoices["period_column"] == "Fecha Efectiva"
    assert len(invoices["columns"]) == 49
    second_warehouse = invoices["columns"][18]
    assert (second_warehouse["header"], second_warehouse["key"]) == ("Almac", "Almac #2")


def test_each_dataset_says_when_it_was_last_loaded(
    client: TestClient, auth_headers: Any, bronze: InMemoryBronze
) -> None:
    from energy.ports.load_committer import DatasetTarget  # noqa: PLC0415

    lines = DatasetTarget("manual_sheets", "Lineas Produccion", "lineas_produccion")
    bronze.add_cli_load(lines, [{"Línea": "FAFORM01"}], content_hash="a" * 64)
    latest = bronze.add_cli_load(lines, [{"Línea": "FAFORM01"}, {"Línea": "ENSAM02"}])
    bronze.add_cli_load(lines, [{"Línea": "X"}], content_hash="b" * 64, state="failed")

    datasets = client.get("/datasets", headers=auth_headers("operator")).json()["datasets"]

    last = datasets[0]["last_load"]
    assert (last["run_id"], last["origin"], last["rows"]) == (str(latest), "cli", 2)
    assert last["filename"] == "EQUS BATT.xlsx"
    assert datasets[1]["last_load"] is None


@pytest.mark.parametrize("key", ["lineas_produccion", "qad_facturas"])
def test_the_template_is_the_dataset_workbook_with_its_headers(
    client: TestClient, auth_headers: Any, key: str
) -> None:
    headers = auth_headers("operator")
    listed = {d["key"]: d for d in client.get("/datasets", headers=headers).json()["datasets"]}

    response = client.get(f"/datasets/{key}/template", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == XLSX
    assert response.headers["content-disposition"] == f'attachment; filename="{key}.xlsx"'
    sheet = CalamineWorkbookReader().open_workbook(response.content).sheet(listed[key]["sheet"])
    (header,) = list(sheet.rows)
    assert list(header_keys(header)) == [column["key"] for column in listed[key]["columns"]]


def test_an_unknown_dataset_has_no_template(client: TestClient, auth_headers: Any) -> None:
    response = client.get("/datasets/nothing/template", headers=auth_headers("operator"))

    assert response.status_code == 404
    assert response.json() == {
        "code": "unknown_dataset",
        "message": "no dataset spec has that key",
        "details": {"dataset_key": "nothing"},
    }


def test_a_malformed_spec_stops_the_api_from_being_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo_root: Path
) -> None:
    from energy.api.main import create_app  # noqa: PLC0415
    from energy.core.settings import Settings  # noqa: PLC0415

    folder = tmp_path / "datasets"
    folder.mkdir()
    for spec in (repo_root / "config/datasets").glob("*.yaml"):
        (folder / spec.name).write_text(spec.read_text(encoding="utf-8"), encoding="utf-8")
    broken = folder / "qad_facturas.yaml"
    broken.write_text(
        broken.read_text(encoding="utf-8").replace(
            "{header: Cantidad fac, type: decimal", "{header: Cantidad fac, type: decimel"
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))

    with pytest.raises(DatasetSpecError) as caught:
        create_app(Settings())

    assert caught.value.problems == (
        f"{broken.as_posix()}: columns[24].type: "
        "'decimel' is not one of text, integer, decimal, date",
    )
