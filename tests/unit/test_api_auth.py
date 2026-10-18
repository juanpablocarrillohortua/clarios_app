"""Who may call which route of the API.

Layer: tests (unit)

The statement of increment 2 of ``docs/frontend-plan.md``: without a token the
API answers 401; a viewer cannot open data loading and an operator can. These
tests call the application through its HTTP surface with a stand-in for the
token verifier (the ``token_verifier`` fixture), so they need neither a database
nor a network. The data-loading and KPI endpoints are still placeholders: for a
caller who is let through they answer 501, "not built yet", which is how these
tests tell "allowed" from "refused".
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest

# Nothing of `energy.api` is imported at the top of this module. Importing
# `energy.api.main` builds the application, which reads and caches the settings;
# pytest imports every test module before it runs any test, so that would freeze
# the database address before the database fixtures of the integration tests
# set it. The imports are inside the functions instead, as in `conftest.py`.

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

TENANT = "11111111-1111-4111-8111-111111111111"
CLIENT = "33333333-3333-4333-8333-333333333333"
WORKSPACE = "44444444-4444-4444-8444-444444444444"
REPORT = "55555555-5555-4555-8555-555555555555"
A_RUN = "0b7a2d0e-6f0f-4c59-9a3e-2f1d4c5b6a70"

GUARDED = ["/me", "/kpis/summary", "/uploads", "/runs", f"/runs/{A_RUN}"]


@pytest.fixture
def entra_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Return a function that sets the app's settings for one test.

    They are the sign-in variables and the two that name the dashboard's
    report. The developer's own ``.env`` may hold real values. The environment
    wins over that file, so each test states what it needs, and an empty value
    means "not set".
    """

    from energy.core.settings import get_settings  # noqa: PLC0415

    def _set(
        tenant_id: str = "", client_id: str = "", workspace_id: str = "", report_id: str = ""
    ) -> None:
        monkeypatch.setenv("ENTRA_TENANT_ID", tenant_id)
        monkeypatch.setenv("ENTRA_CLIENT_ID", client_id)
        monkeypatch.setenv("POWERBI_WORKSPACE_ID", workspace_id)
        monkeypatch.setenv("POWERBI_REPORT_ID", report_id)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


# =============================================================================
# Without a valid token: 401
# =============================================================================
@pytest.mark.parametrize("path", GUARDED)
def test_without_a_token_a_guarded_route_answers_401(
    client: TestClient, token_verifier: Any, path: str
) -> None:
    response = client.get(path)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {
        "code": "unauthenticated",
        "message": "Sign in to call this endpoint",
        "details": {},
    }


def test_writing_without_a_token_answers_401(client: TestClient, token_verifier: Any) -> None:
    assert client.post(f"/runs/{A_RUN}/retry").status_code == 401
    assert client.post("/uploads").status_code == 401
    assert client.post("/drafts?dataset_key=lineas_produccion", content=b"PK").status_code == 401
    assert client.post(f"/drafts/{A_RUN}/validate").status_code == 401


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Bearer", "token-without-a-scheme"])
def test_a_header_that_carries_no_bearer_token_is_no_token(
    client: TestClient, token_verifier: Any, header: str
) -> None:
    response = client.get("/me", headers={"Authorization": header})

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


def test_a_refused_token_answers_401_and_does_not_say_why(
    client: TestClient, token_verifier: Any
) -> None:
    """The reason is for the log. Told to the caller, it would guide an attacker."""
    response = client.get("/me", headers={"Authorization": "Bearer a-forged-token"})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Bearer error="invalid_token"'
    assert response.json() == {
        "code": "unauthenticated",
        "message": "The access token was refused",
        "details": {},
    }
    assert "bad_signature" not in response.text
    assert "a-forged-token" not in response.text


def test_no_token_is_a_401_even_when_sign_in_is_not_configured(
    client: TestClient, entra_env: Any
) -> None:
    entra_env()

    assert client.get("/me").status_code == 401


# =============================================================================
# A token that cannot be checked: 503, not 401
# =============================================================================
def test_a_token_for_a_server_without_sign_in_settings_answers_503(
    client: TestClient, entra_env: Any
) -> None:
    """The real binding, with nothing to bind: the caller is not at fault."""
    entra_env()

    response = client.get("/me", headers={"Authorization": "Bearer any-token"})

    assert response.status_code == 503
    assert response.json()["code"] == "auth_not_configured"


def test_a_token_that_cannot_be_checked_answers_503(
    client: TestClient, token_verifier: Any, auth_headers: Any
) -> None:
    headers = auth_headers("viewer")
    token_verifier.refuse_all()

    response = client.get("/me", headers=headers)

    assert response.status_code == 503
    assert response.json()["code"] == "identity_provider_unavailable"


# =============================================================================
# GET /me
# =============================================================================
def test_me_tells_a_viewer_what_they_may_do(client: TestClient, auth_headers: Any) -> None:
    response = client.get("/me", headers=auth_headers("viewer"))

    assert response.status_code == 200
    assert response.json() == {
        "subject": "00000000-0000-4000-8000-00000000abcd",
        "name": "Ada Lovelace",
        "username": "ada@example.test",
        "roles": ["viewer"],
        "permissions": ["dashboard:view"],
        "plant_scope": ["1515"],
    }


@pytest.mark.parametrize("role", ["operator", "engineer", "admin"])
def test_me_tells_who_may_load_data(client: TestClient, auth_headers: Any, role: str) -> None:
    payload = client.get("/me", headers=auth_headers(role)).json()

    assert payload["roles"] == [role]
    assert payload["permissions"] == ["dashboard:view", "loads:write"]


def test_me_answers_a_person_without_a_role_with_no_permission(
    client: TestClient, auth_headers: Any
) -> None:
    """Signed in is enough to ask. The app then shows the no-access page."""
    response = client.get("/me", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["roles"] == []
    assert response.json()["permissions"] == []


def test_me_ignores_a_role_value_the_application_does_not_define(
    client: TestClient, auth_headers: Any
) -> None:
    payload = client.get("/me", headers=auth_headers("superuser", "device", "viewer")).json()

    assert payload["roles"] == ["viewer"]


# =============================================================================
# A viewer cannot open data loading; an operator can
# =============================================================================
@pytest.mark.parametrize(
    "path",
    [
        "/uploads",
        "/runs",
        f"/runs/{A_RUN}",
        "/datasets",
        "/datasets/qad_facturas/template",
        f"/drafts/{A_RUN}",
        f"/drafts/{A_RUN}/issues",
    ],
)
def test_a_viewer_is_refused_on_data_loading(
    client: TestClient, auth_headers: Any, path: str
) -> None:
    response = client.get(path, headers=auth_headers("viewer"))

    assert response.status_code == 403
    assert response.json() == {
        "code": "forbidden",
        "message": "the caller may not do this",
        "details": {"permission": "loads:write"},
    }


@pytest.mark.parametrize("role", ["operator", "engineer", "admin"])
@pytest.mark.parametrize("path", ["/uploads", "/runs", f"/runs/{A_RUN}"])
def test_an_operator_and_above_reach_data_loading(
    client: TestClient, auth_headers: Any, role: str, path: str
) -> None:
    response = client.get(path, headers=auth_headers(role))

    assert response.status_code == 501
    assert response.json()["code"] == "not_implemented"


def test_a_viewer_may_not_upload_and_the_file_is_not_read(
    client: TestClient, auth_headers: Any
) -> None:
    response = client.post(
        "/drafts?dataset_key=lineas_produccion",
        content=b"PK not read",
        headers={**auth_headers("viewer"), "X-File-Name": "a.xlsx"},
    )

    assert response.status_code == 403


def test_only_an_operator_and_above_may_retry_a_run(client: TestClient, auth_headers: Any) -> None:
    path = f"/runs/{A_RUN}/retry"

    assert client.post(path, headers=auth_headers("viewer")).status_code == 403
    assert client.post(path, headers=auth_headers("operator")).status_code == 501


@pytest.mark.parametrize("role", ["viewer", "operator", "engineer", "admin"])
def test_every_role_of_people_reaches_the_figures(
    client: TestClient, auth_headers: Any, role: str
) -> None:
    assert client.get("/kpis/summary", headers=auth_headers(role)).status_code == 501


@pytest.mark.parametrize("path", ["/kpis/summary", "/uploads", "/runs"])
def test_a_person_without_a_role_is_refused_everywhere_but_me(
    client: TestClient, auth_headers: Any, path: str
) -> None:
    assert client.get(path, headers=auth_headers()).status_code == 403


def test_a_refusal_is_logged_with_the_caller_and_without_the_token(
    client: TestClient,
    token_verifier: Any,
    auth_headers: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    headers = auth_headers("viewer")

    client.get("/uploads", headers=headers)
    client.get("/me", headers={"Authorization": "Bearer a-forged-token"})

    assert token_verifier.subject in caplog.text
    assert headers["Authorization"].removeprefix("Bearer ") not in caplog.text
    assert "a-forged-token" not in caplog.text


# =============================================================================
# Public routes
# =============================================================================
@pytest.mark.parametrize("path", ["/health", "/config", "/openapi.json", "/docs"])
def test_public_routes_need_no_token(client: TestClient, token_verifier: Any, path: str) -> None:
    assert client.get(path).status_code == 200


def test_config_gives_the_app_its_sign_in_settings(client: TestClient, entra_env: Any) -> None:
    # Upper case on purpose: the identifiers are returned in lower case.
    entra_env(tenant_id=TENANT.upper(), client_id=CLIENT.upper())

    response = client.get("/config")

    assert response.status_code == 200
    assert response.json() == {
        "auth": {
            "tenant_id": TENANT,
            "client_id": CLIENT,
            "authority": f"https://login.microsoftonline.com/{TENANT}",
            "api_scope": f"api://{CLIENT}/access_as_user",
        },
        "powerbi": None,
    }


@pytest.mark.parametrize(
    ("tenant_id", "client_id"), [("", ""), (TENANT, ""), ("", CLIENT), ("  ", "  ")]
)
def test_config_says_when_sign_in_is_not_configured(
    client: TestClient, entra_env: Any, tenant_id: str, client_id: str
) -> None:
    entra_env(tenant_id=tenant_id, client_id=client_id)

    response = client.get("/config")

    assert response.status_code == 200
    assert response.json() == {"auth": None, "powerbi": None}


def test_config_never_carries_the_client_secret(
    client: TestClient, entra_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "a-secret-that-must-stay-put")
    entra_env(tenant_id=TENANT, client_id=CLIENT, workspace_id=WORKSPACE, report_id=REPORT)

    assert "a-secret-that-must-stay-put" not in client.get("/config").text


def test_config_tells_the_app_which_report_the_dashboard_shows(
    client: TestClient, entra_env: Any
) -> None:
    """The app is handed whole addresses, so it holds no address of Power BI's itself."""
    # Upper case on purpose: the identifiers are returned in lower case.
    entra_env(
        tenant_id=TENANT,
        client_id=CLIENT,
        workspace_id=WORKSPACE.upper(),
        report_id=REPORT.upper(),
    )

    response = client.get("/config")

    assert response.status_code == 200
    assert response.json()["powerbi"] == {
        "workspace_id": WORKSPACE,
        "report_id": REPORT,
        "scope": "https://analysis.windows.net/powerbi/api/Report.Read.All",
        "report_api_url": (
            f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE}/reports/{REPORT}"
        ),
        "report_web_url": f"https://app.powerbi.com/groups/{WORKSPACE}/reports/{REPORT}",
    }


@pytest.mark.parametrize(
    ("workspace_id", "report_id"), [("", ""), (WORKSPACE, ""), ("", REPORT), ("  ", "  ")]
)
def test_config_says_when_the_report_is_not_configured(
    client: TestClient, entra_env: Any, workspace_id: str, report_id: str
) -> None:
    """Sign-in does not depend on the report: the rest of the app must still start."""
    entra_env(tenant_id=TENANT, client_id=CLIENT, workspace_id=workspace_id, report_id=report_id)

    body = client.get("/config").json()

    assert body["powerbi"] is None
    assert body["auth"] is not None


def test_config_names_the_report_even_when_sign_in_is_not_configured(
    client: TestClient, entra_env: Any
) -> None:
    """The two halves are independent; neither is derived from the other."""
    entra_env(workspace_id=WORKSPACE, report_id=REPORT)

    body = client.get("/config").json()

    assert body["auth"] is None
    assert body["powerbi"]["report_id"] == REPORT


# =============================================================================
# The policy as a whole
# =============================================================================
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def test_every_route_says_who_may_call_it(
    app: FastAPI, client: TestClient, token_verifier: Any
) -> None:
    """A route is public by name, for devices, or refuses a caller without a token.

    This is what keeps a new endpoint from being left open by oversight: a
    router mounted without a permission, and not listed as public, fails here.
    Every operation the API publishes is called without a token; the pages of
    its own documentation, which are not operations, must be on the public list.
    """
    from energy.api.main import DEVICE_PATH_PREFIX, PUBLIC_PATHS  # noqa: PLC0415

    operations = [
        (path, method)
        for path, item in app.openapi()["paths"].items()
        for method in item
        if method in HTTP_METHODS
    ]
    assert operations, "the API publishes no operation"

    open_to_all: set[str] = set()
    left_open: list[str] = []
    for path, method in operations:
        if path.startswith(DEVICE_PATH_PREFIX):
            continue
        if path in PUBLIC_PATHS:
            open_to_all.add(path)
            continue
        answer = client.request(method.upper(), re.sub(r"\{[^}]+\}", A_RUN, path))
        if answer.status_code != 401:
            left_open.append(f"{method.upper()} {path} -> {answer.status_code}")

    assert not left_open, f"operations that answer a caller without a token: {left_open}"

    pages = {path for route in app.routes if (path := getattr(route, "path", None))}
    assert pages <= PUBLIC_PATHS, f"pages that are not listed as public: {pages - PUBLIC_PATHS}"
    # Nothing in the list is stale, so the list is the whole truth.
    assert open_to_all | pages == PUBLIC_PATHS
