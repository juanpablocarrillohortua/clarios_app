"""Command-line entry point for the platform.

Layer: api (entry point)

Responsibility:
    Exposes the operational actions an engineer needs from a shell: serve the
    API or the dashboards, run a pipeline once, seed reference data, inspect
    configuration, and land source workbooks in Bronze. It is a thin Typer
    wrapper - every command parses arguments, calls one service or flow, and
    prints the result. No business logic, no SQL, and no direct adapter
    construction; it resolves everything through the same dependency wiring the
    pipelines use, so a command and a scheduled run can never behave
    differently.

    The ``equs`` commands are implemented. The other commands are placeholders
    for the phase named in each, and are not registered until they are built.

Depends on:
    ``energy.core``, ``energy.domain``, ``energy.pipelines.flows``, ``typer``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from energy.core.logging import configure_from_settings
from energy.domain.exceptions import EnergyError
from energy.domain.landing import DEFAULT_PLANT_CODE, LANDING_SOURCES, parse_sheet_spec
from energy.pipelines.flows.equs_bronze_load import (
    DEFAULT_DIRECTORY,
    RouteOutcome,
    build_loader,
    load_all,
    load_workbook,
)
from energy.services.bronze_loader import LoadResult

_DATE_FORMAT = "%Y-%m-%d"
_SOURCE_KEYS = ", ".join(LANDING_SOURCES)


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


def _fail(error: EnergyError) -> typer.Exit:
    """Print a deliberate error and return the exit that reports it."""
    typer.secho(f"error: {error}", err=True, fg=typer.colors.RED)
    return typer.Exit(code=1)


def _print_result(result: LoadResult) -> None:
    """Print one line per sheet of a load, then the period its run covers."""
    for sheet in result.sheets:
        label = f"{sheet.sheet_name}={sheet.dataset_key}" if sheet.dataset_key else sheet.sheet_name
        detail = "already landed, skipped" if sheet.skipped else f"{sheet.rows:,} rows"
        typer.echo(f"  {label:<38} {detail}")
    if result.window_start or result.window_end:
        start = result.window_start.isoformat() if result.window_start else "?"
        end = result.window_end.isoformat() if result.window_end else "?"
        typer.echo(f"  {'period':<38} {start} to {end}")


def equs_load(
    source_key: Annotated[
        str,
        typer.Argument(help=f"Which kind of export the workbook is: {_SOURCE_KEYS}."),
    ],
    file: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="The workbook to land. It is only read, never modified.",
        ),
    ],
    sheet: Annotated[
        list[str] | None,
        typer.Option(
            "--sheet",
            "-s",
            help=(
                "A sheet to land, as NAME[=dataset_key][:header_row]. Repeat for several. "
                "Omit to land the source's default sheets."
            ),
        ),
    ] = None,
    header_row: Annotated[
        int,
        typer.Option(
            "--header-row",
            min=0,
            help="Header row of the sheets that name none; 0 means no header row.",
        ),
    ] = 1,
    plant_code: Annotated[
        str, typer.Option("--plant-code", help="The plant the data belongs to.")
    ] = DEFAULT_PLANT_CODE,
    flow_name: Annotated[
        str | None,
        typer.Option("--flow-name", help="Name the run is recorded under. Default: the file stem."),
    ] = None,
    window_start: Annotated[
        datetime | None,
        typer.Option(
            "--window-start",
            formats=[_DATE_FORMAT],
            help="First date of the business period, replacing the dates found in the rows.",
        ),
    ] = None,
    window_end: Annotated[
        datetime | None,
        typer.Option(
            "--window-end",
            formats=[_DATE_FORMAT],
            help="Last date of the business period, replacing the dates found in the rows.",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Land the sheets again under a new run."),
    ] = False,
    archive_dir: Annotated[
        Path | None,
        typer.Option(
            "--archive-dir",
            file_okay=False,
            help="Archive the original into this directory instead of the configured backend.",
        ),
    ] = None,
    uploaded_by: Annotated[
        str, typer.Option("--uploaded-by", help="Who is submitting the file.")
    ] = "system",
) -> None:
    """Land one workbook, or some of its sheets, in Bronze."""
    try:
        specs = [parse_sheet_spec(text, default_header_row=header_row) for text in sheet or []]
        result = load_workbook(
            build_loader(archive_dir=archive_dir),
            source_key,
            file,
            specs,
            plant_code=plant_code,
            flow_name=flow_name,
            window_start=window_start.date() if window_start else None,
            window_end=window_end.date() if window_end else None,
            force=force,
            uploaded_by=uploaded_by,
        )
    except EnergyError as error:
        raise _fail(error) from error

    typer.echo(f"{result.source_key}  {result.filename}")
    _print_result(result)
    if result.skipped:
        typer.echo("Nothing to do: every requested sheet was already landed by a completed run.")
    else:
        typer.echo(f"Landed {result.rows_landed:,} rows in run {result.run_id}.")


def _print_outcome(outcome: RouteOutcome) -> None:
    """Print what happened to one load of a load-all."""
    route = outcome.route
    if outcome.status == "missing":
        note = "not delivered yet, skipped" if route.optional else "MISSING"
        typer.echo(f"{route.source_key}  {route.filename}: {note}")
        return

    name = outcome.path.name if outcome.path else route.filename
    typer.echo(f"{route.source_key}  {name}  ({outcome.seconds:.1f} s)")
    if outcome.result is not None:
        _print_result(outcome.result)
    if outcome.status == "failed":
        typer.secho(f"  FAILED: {outcome.error}", err=True, fg=typer.colors.RED)


def equs_load_all(
    directory: Annotated[
        Path,
        typer.Option(
            "--dir",
            file_okay=False,
            help="Where the workbooks are. They are only read, never modified.",
        ),
    ] = DEFAULT_DIRECTORY,
    plant_code: Annotated[
        str, typer.Option("--plant-code", help="The plant the data belongs to.")
    ] = DEFAULT_PLANT_CODE,
    include_optional: Annotated[
        bool,
        typer.Option(
            "--include-optional",
            help="Also land the optional sheets (L.PRODUCCION of EQUS BATT).",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Land everything again under new runs."),
    ] = False,
    archive_dir: Annotated[
        Path | None,
        typer.Option(
            "--archive-dir",
            file_okay=False,
            help="Archive the originals into this directory instead of the configured backend.",
        ),
    ] = None,
    uploaded_by: Annotated[
        str, typer.Option("--uploaded-by", help="Who is submitting the files.")
    ] = "system",
) -> None:
    """Land every workbook of the monthly close found in a directory.

    The transactions and invoices exports may be there several times, one file
    per period, named with the usual name followed by anything; each is landed,
    whatever years it holds.
    """
    if not directory.is_dir():
        typer.secho(f"error: no such directory: {directory}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1)

    try:
        loader = build_loader(archive_dir=archive_dir)
    except EnergyError as error:
        raise _fail(error) from error

    summary = load_all(
        loader,
        directory,
        plant_code=plant_code,
        force=force,
        include_optional=include_optional,
        uploaded_by=uploaded_by,
        on_outcome=_print_outcome,
    )

    counts = {
        status: sum(1 for outcome in summary.outcomes if outcome.status == status)
        for status in ("landed", "skipped", "failed")
    }
    missing = [o.route.filename for o in summary.outcomes if o.status == "missing"]
    required_missing = [
        o.route.filename for o in summary.outcomes if o.status == "missing" and not o.route.optional
    ]
    typer.echo(
        f"Landed {summary.rows_landed:,} rows in {summary.seconds:.1f} s. "
        f"Loads: {counts['landed']} landed, {counts['skipped']} already landed, "
        f"{counts['failed']} failed. Workbooks not found: {len(missing)}."
    )
    if summary.unmatched:
        names = ", ".join(path.name for path in summary.unmatched)
        typer.echo(f"Not loaded, no route takes them: {names}")
    if required_missing:
        typer.secho(
            f"Missing workbooks: {', '.join(required_missing)}", err=True, fg=typer.colors.RED
        )
    if not summary.ok:
        raise typer.Exit(code=1)


def build_app() -> typer.Typer:
    """Build the Typer application with every implemented command.

    Returns:
        The application. ``energy equs load`` lands one workbook and
        ``energy equs load-all`` lands a directory of them.
    """
    app = typer.Typer(
        name="energy",
        help="Operational commands for the Clarios Energy platform.",
        no_args_is_help=True,
        add_completion=False,
    )
    equs = typer.Typer(
        help="Equivalent-batteries (EQUS) data: land the source workbooks in Bronze.",
        no_args_is_help=True,
    )
    equs.command("load")(equs_load)
    equs.command("load-all")(equs_load_all)
    app.add_typer(equs, name="equs")
    return app


def main() -> None:
    """Build the Typer application and dispatch the requested command."""
    configure_from_settings()
    build_app()()


if __name__ == "__main__":
    main()
