"""Command-line entry point for the platform.

Layer: api (entry point)

Responsibility:
    Exposes the operational actions an engineer needs from a shell: serve the
    API or the dashboards, run a pipeline once, seed reference data, and inspect
    configuration. It is a thin Typer wrapper - every command parses arguments,
    calls one service or flow, and prints the result. No business logic, no SQL,
    and no direct adapter construction; it resolves everything through the same
    dependency wiring the API uses, so a command and an endpoint can never
    behave differently.

Depends on:
    ``energy.core``, ``energy.api.main``, ``energy.dashboards.app``,
    ``energy.pipelines.flows``, ``typer``.

Implemented in:
    Phase 2 - Domain model and KPI engine
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


def serve_api(host: str | None = None, port: int | None = None, reload: bool = False) -> None:
    """Run the FastAPI application with Uvicorn.

    Args:
        host: interface to bind; defaults to the configured ``API_HOST``.
        port: port to bind; defaults to the configured ``API_PORT``.
        reload: enable auto-reload. Development only - it forks a reloader
            process that breaks connection pooling.
    """
    raise NotImplementedError("Phase 2: API server command")


def serve_dashboards(host: str | None = None, port: int | None = None) -> None:
    """Run the Plotly Dash application.

    Args:
        host: interface to bind; defaults to the configured ``DASH_HOST``.
        port: port to bind; defaults to the configured ``DASH_PORT``.
    """
    raise NotImplementedError("Phase 5: dashboard server command")


def run_flow(flow_name: str, plant_code: str, start: datetime, end: datetime) -> None:
    """Execute one Prefect flow synchronously.

    The path for re-running a failed nightly load by hand, without waiting for
    the schedule or clicking through the Prefect UI.

    Args:
        flow_name: which flow to run.
        plant_code: the plant to run it for.
        start: period start, inclusive, timezone-aware.
        end: period end, exclusive, timezone-aware.

    Raises:
        ConfigurationError: if the flow name is unknown.
        PipelineError: if the run fails; the exit code is non-zero so a cron
            wrapper notices.
    """
    raise NotImplementedError("Phase 3: manual flow execution command")


def show_config() -> None:
    """Print the effective configuration with secrets redacted.

    The first thing to run when an environment behaves unexpectedly: it shows
    what the process actually resolved, not what the ``.env`` file appears to say.
    """
    raise NotImplementedError("Phase 2: configuration inspection command")


def check_sources() -> None:
    """Validate ``sources.yaml`` and probe every configured source.

    Raises:
        ConfigurationError: if the configuration is invalid.
        SourceUnavailableError: if a source fails its health check; the exit code
            is non-zero so this can gate a deployment.
    """
    raise NotImplementedError("Phase 3: source health command")


def main() -> None:
    """Build the Typer application and dispatch the requested command."""
    raise NotImplementedError("Phase 2: CLI entry point")


if __name__ == "__main__":
    main()
