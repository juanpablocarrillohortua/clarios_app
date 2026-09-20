"""End-to-end test that the full docker compose stack boots and serves traffic.

Layer: tests (e2e)

Responsibility:
    Verifies the deployment topology rather than any business behaviour: that
    every container reaches a healthy state, that Caddy is the only reachable
    ingress, and that each service answers through its configured route. This is
    the test that catches a broken Dockerfile, a misrouted proxy rule, or a
    service that starts but never becomes healthy - failures no unit or
    integration test can see.

    Marked ``e2e`` and excluded from ``make test-unit``. It requires the stack to
    be running (``make up``) and is skipped otherwise, so it never fails a run on
    a machine that simply has not started the stack.

Depends on:
    A running compose stack, ``httpx``, ``docker``.

Implemented in:
    Phase 2 - Domain model and KPI engine
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

PHASE = "Phase 2: end-to-end stack tests are not implemented yet"


@pytest.mark.skip(reason=PHASE)
def test_all_services_report_healthy() -> None:
    """Every compose service reaches state `healthy`.

    Reads `docker compose ps --format json` and asserts that caddy, api, dash,
    db, minio, prefect-server and prefect-worker all report healthy within the
    startup timeout.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_only_caddy_publishes_ports() -> None:
    """No service other than caddy publishes a host port.

    The network policy is a security property, not a preference: a database
    reachable from the host on a developer laptop becomes a database reachable
    from the network on a shared host.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_api_is_reachable_through_the_proxy() -> None:
    """GET /api/health through Caddy returns 200.

    Also asserts that the response carries an X-Request-ID header, proving the
    middleware runs behind the proxy.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_api_docs_render_behind_the_prefix() -> None:
    """GET /api/docs returns the OpenAPI UI with correctly prefixed URLs.

    Catches a root_path misconfiguration, which breaks the docs page while
    leaving every API route working - so nothing else would notice.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_dashboards_are_reachable_at_the_root() -> None:
    """GET / through Caddy returns the Dash application."""
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_prefect_ui_is_reachable() -> None:
    """GET /prefect/ through Caddy returns the Prefect UI."""
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_minio_s3_api_is_not_exposed() -> None:
    """The MinIO S3 API is not reachable through the proxy.

    Only the console is proxied. Object storage credentials grant access to
    every archived source file, so the S3 endpoint stays internal.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_readiness_reports_timescaledb() -> None:
    """GET /api/ready reports the timescaledb extension as installed.

    Proves the migrations ran against the real stack, not just in a test
    container.
    """
    raise AssertionError("not implemented")


@pytest.mark.skip(reason=PHASE)
def test_stack_survives_a_database_restart() -> None:
    """The API recovers after the database container restarts.

    `pool_pre_ping` exists precisely for this; without it every pooled
    connection stays broken until the API itself is restarted.
    """
    raise AssertionError("not implemented")
