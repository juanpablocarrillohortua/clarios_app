"""Verify the liveness and readiness endpoints against a real database.

Layer: tests (integration)

The two endpoints have deliberately different contracts, and the distinction is
what these tests pin down:

``/health``
    Must answer even when every dependency is down. If it touched the database,
    a database outage would make the orchestrator restart healthy API
    containers, turning a degraded system into an unavailable one.

``/ready``
    Must reflect dependency state, returning 503 only when the instance genuinely
    cannot serve traffic. A freshly migrated database with no data yet is
    *degraded*, not unhealthy - refusing traffic then would block the very
    pipelines that are about to load it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from energy.core.constants import REQUEST_ID_HEADER

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

# Markers are applied per test rather than to the module: the liveness cases
# genuinely need no database and must still run under `make test-unit`, while
# the readiness cases require one. A module-level `integration` mark would
# deselect the liveness tests from every unit run.


# =============================================================================
# Liveness - no database required
# =============================================================================
@pytest.mark.unit
def test_health_returns_ok_without_a_database(client: TestClient) -> None:
    """Liveness answers 200 with no database configured at all."""
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"]
    assert payload["version"]


@pytest.mark.unit
def test_health_echoes_a_request_id(client: TestClient) -> None:
    """Every response carries a correlation id the caller can quote."""
    response = client.get("/health")

    assert REQUEST_ID_HEADER in response.headers
    assert response.headers[REQUEST_ID_HEADER]


@pytest.mark.unit
def test_health_preserves_an_inbound_request_id(client: TestClient) -> None:
    """A caller-supplied correlation id is propagated rather than replaced.

    This is what lets a trace span the reverse proxy, the API and the pipelines.
    """
    supplied = "11111111-2222-3333-4444-555555555555"
    response = client.get("/health", headers={REQUEST_ID_HEADER: supplied})

    assert response.headers[REQUEST_ID_HEADER] == supplied


# =============================================================================
# Readiness - requires a database
# =============================================================================
@pytest.mark.integration
def test_ready_reports_healthy_against_a_migrated_database(
    migrated_database: str, patched_settings_db: str, client: TestClient
) -> None:
    """Readiness reports healthy or degraded once the schema exists.

    Degraded is the expected state for a migrated but unloaded database: the
    freshness probe finds no Silver rows yet, which is correct and must not
    block traffic.
    """
    response = client.get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"healthy", "degraded"}

    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["connectivity"]["status"] == "healthy"


@pytest.mark.integration
def test_ready_confirms_timescaledb_is_installed(
    migrated_database: str, patched_settings_db: str, client: TestClient
) -> None:
    """The extension probe reports timescaledb and pgcrypto as present."""
    response = client.get("/ready")
    payload = response.json()

    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["extensions"]["status"] == "healthy"
    assert "timescaledb" in checks["extensions"]["data"]


@pytest.mark.integration
def test_ready_reports_freshness_of_an_empty_silver_layer(
    migrated_database: str, patched_settings_db: str, client: TestClient
) -> None:
    """An empty Silver layer is reported as degraded, not unhealthy."""
    response = client.get("/ready")
    payload = response.json()

    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["freshness"]["status"] == "degraded"


@pytest.mark.integration
def test_ready_returns_503_when_the_database_is_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Readiness returns 503 so a load balancer takes the instance out of rotation.

    Uses a DSN pointing at a closed port, which is the closest reproduction of a
    database outage that does not require stopping a container.
    """
    from energy.core.settings import get_settings  # noqa: PLC0415
    from energy.db.session import dispose_engine  # noqa: PLC0415

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody:nobody@127.0.0.1:1/absent")
    get_settings.cache_clear()
    dispose_engine()

    try:
        response = client.get("/ready")

        assert response.status_code == 503
        payload = response.json()
        assert payload["status"] == "unhealthy"

        checks = {check["name"]: check for check in payload["checks"]}
        assert checks["connectivity"]["status"] == "unhealthy"
    finally:
        dispose_engine()
        get_settings.cache_clear()


@pytest.mark.integration
def test_health_still_answers_when_the_database_is_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Liveness is unaffected by a database outage.

    The most important assertion in this module: if this ever fails, a database
    outage will cause the orchestrator to restart every API container.
    """
    from energy.core.settings import get_settings  # noqa: PLC0415
    from energy.db.session import dispose_engine  # noqa: PLC0415

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody:nobody@127.0.0.1:1/absent")
    get_settings.cache_clear()
    dispose_engine()

    try:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    finally:
        dispose_engine()
        get_settings.cache_clear()


# =============================================================================
# Placeholder endpoints
# =============================================================================
@pytest.mark.unit
def test_unimplemented_endpoints_return_501(client: TestClient, auth_headers: Any) -> None:
    """A Phase 1 placeholder endpoint reports 501, not 500.

    A call to an unbuilt endpoint must read as "not built yet" rather than
    "server bug", and the message names the phase that will build it. The
    caller is signed in: without that the answer is 401, before the endpoint is
    reached (``tests/unit/test_api_auth.py``).
    """
    response = client.get("/kpis/summary", headers=auth_headers("viewer"))

    assert response.status_code == 501
    payload = response.json()
    assert payload["code"] == "not_implemented"
    assert "Phase" in payload["message"]
