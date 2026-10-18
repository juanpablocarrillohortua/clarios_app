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

    The ``equs`` commands are implemented, and so is ``equs-report``, a command
    of its own. The other commands are placeholders for the phase named in each,
    and are not registered until they are built.

    Every command logs to standard error, so that standard output carries only
    its result: a table, CSV or JSON that can be piped.

    Exit codes of the EQUS Gold commands (``refresh-gold``, ``resumen-equ``,
    ``close-month``, ``daily``, ``cruce``, ``equs-report``): 0 success; 1 invalid
    arguments, an unreadable export, or a request refused (a month already
    closed, a blocking data-quality check, days that do not add up to their
    month);
    2 a database error; 3 the refresh lock was not released in time. A
    malformed command line exits 1 too, rather than Click's usual 2, so that 2
    always means the database.

Depends on:
    ``energy.core``, ``energy.domain``, ``energy.pipelines.flows``, ``typer``.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Final

import typer

from energy.core.logging import configure_from_settings
from energy.domain.energy_cross import cross_csv_rows, cross_json, describe_cross
from energy.domain.equs_gold import (
    COLUMN_H_FIELDS,
    REPORT_SOURCES,
    DateRange,
    describe_close,
    describe_quality,
    month_label,
    parse_day,
    parse_month,
)
from energy.domain.equs_series import (
    FREQUENCIES,
    describe_series,
    series_csv_rows,
    series_json,
)
from energy.domain.equs_silver import DEFAULT_EXAMPLES, describe
from energy.domain.exceptions import (
    EnergyError,
    InvalidPeriodError,
    LockTimeoutError,
    RepositoryError,
    SourceUnavailableError,
)
from energy.domain.landing import DEFAULT_PLANT_CODE, LANDING_SOURCES, parse_sheet_spec
from energy.pipelines.flows.energy_cross import DEFAULT_DATA_DIRECTORY, energy_cross
from energy.pipelines.flows.equs_bronze_load import (
    DEFAULT_DIRECTORY,
    RouteOutcome,
    build_loader,
    load_all,
    load_workbook,
)
from energy.pipelines.flows.equs_gold import (
    battery_equivalents,
    close_month,
    latest_production_month,
    refresh_gold,
    resumen_equ,
)
from energy.pipelines.flows.equs_report import report_for
from energy.pipelines.flows.equs_silver_build import build_silver
from energy.services.bronze_loader import LoadResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from energy.domain.equs_gold import ColumnHRow, QualityCount, ReportSource

_DATE_FORMAT = "%Y-%m-%d"
_SOURCE_KEYS = ", ".join(LANDING_SOURCES)

# Exit codes of the EQUS Gold commands.
EXIT_INVALID: Final = 1
EXIT_DATABASE: Final = 2
EXIT_LOCKED: Final = 3

_TABLE_FORMATS: Final = ("table", "json", "csv")
# The exit code Click gives a malformed command line.
_CLICK_USAGE_ERROR: Final = 2


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


def equs_build_silver(
    examples: Annotated[
        int,
        typer.Option(
            "--examples",
            min=0,
            help="How many examples of each finding to list.",
        ),
    ] = DEFAULT_EXAMPLES,
) -> None:
    """Bring Silver up to date with Bronze and print the data-quality report.

    Typed, deduplicated tables are rebuilt from the rows the completed loads
    landed. It is one transaction, and running it again changes nothing.
    """
    try:
        report = build_silver()
    except EnergyError as error:
        raise _fail(error) from error

    for line in describe(report, examples=examples):
        typer.echo(line)
    typer.echo("")
    typer.echo(f"Silver built in {report.seconds:.1f} s.")


# =============================================================================
# EQUS Gold
# =============================================================================
def _exit_code(error: EnergyError) -> int:
    """Return the exit code an error of the EQUS Gold commands ends with."""
    if isinstance(error, LockTimeoutError):
        return EXIT_LOCKED
    if isinstance(error, RepositoryError):
        return EXIT_DATABASE
    return EXIT_INVALID


def _stop(error: EnergyError) -> typer.Exit:
    """Print an error of an EQUS Gold command and return the exit that reports it."""
    typer.secho(f"error: {error}", err=True, fg=typer.colors.RED)
    return typer.Exit(code=_exit_code(error))


def _choice(value: str, choices: Sequence[str], option: str) -> str:
    """Check the value of an option that takes one of a few words."""
    if value.lower() not in choices:
        raise InvalidPeriodError(f"{option} takes one of: {', '.join(choices)}", value=value)
    return value.lower()


def _source(value: str) -> ReportSource:
    return "closed" if _choice(value, REPORT_SOURCES, "--source") == "closed" else "live"


def _number(value: float | None, decimals: int, *, fixed: bool = False) -> str:
    """Write a number of the column-H table; blank when there is none.

    Quantities and factors drop trailing zeros; EQ (``fixed``) always shows its
    decimals, as the workbook does.
    """
    if value is None:
        return ""
    if fixed:
        return f"{value:,.{decimals}f}"
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.{decimals}f}".rstrip("0").rstrip(".")


def _column_h_table(rows: Sequence[ColumnHRow], month: str, source: str) -> list[str]:
    """Render the column-H rows of a month as an aligned table."""
    lines = [
        f"Column H of RESUMEN EQU, {month} ({source})",
        "",
        f"{'row':>4}  {'cuenta STATS':<17} {'process':<10} {'plant':<10} "
        f"{'quantity (D)':>18} {'factor (G)':>14} {'EQ (H)':>14}",
    ]
    headings = {"total": "Totals", "poly": "EQUS POLY (warehouse 1010)"}
    for row in rows:
        heading = headings.pop(row.tipo, None)
        if heading:
            lines += ["", heading]
        if row.tipo == "total":
            lines.append(
                f"{'':>4}  {row.nombre:<17} {row.proceso or '':<10} {row.planta or '':<10} "
                f"{_number(row.cantidad, 6):>18} {'':>14} {_number(row.equs, 2, fixed=True):>14}"
            )
            continue
        label = row.categoria if row.tipo == "poly" else row.proceso
        lines.append(
            f"{row.fila if row.fila is not None else '':>4}  {row.nombre:<17} {label or '':<10} "
            f"{row.planta or '':<10} {_number(row.cantidad, 6):>18} "
            f"{_number(row.factor, 10):>14} {_number(row.equs, 2, fixed=True):>14}"
        )
    return lines


def _print_column_h(
    report: dict[str, Any], rows: Sequence[ColumnHRow], output_format: str, *, extra: Any = None
) -> None:
    """Print a month's column H as a table, JSON or CSV."""
    month = report["rango"]["meses"][0]
    if output_format == "json":
        payload: dict[str, Any] = {
            "mes": month,
            "fuente": report["fuente"],
            "versiones": report["versiones"],
            "filas": [row.as_dict() for row in rows],
            "calidad_datos": report["calidad_datos"],
            "advertencias": report["advertencias"],
        }
        if extra is not None:
            payload = extra | {"resumen": payload}
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    elif output_format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(COLUMN_H_FIELDS)
        for row in rows:
            values = row.as_dict()
            writer.writerow("" if values[key] is None else values[key] for key in COLUMN_H_FIELDS)
        typer.echo(buffer.getvalue(), nl=False)
    else:
        for line in _column_h_table(rows, month, report["fuente"]):
            typer.echo(line)
        for warning in report["advertencias"]:
            typer.secho(f"warning: {warning['mensaje']}", err=True, fg=typer.colors.YELLOW)


def equs_refresh_gold(
    with_silver: Annotated[
        bool,
        typer.Option(
            "--with-silver", help="Build Silver from Bronze first, in its own transaction."
        ),
    ] = False,
    month: Annotated[
        str | None,
        typer.Option(
            "--month",
            help="Month of the column-H table printed at the end, as YYYY-MM. "
            "Default: the latest month with production.",
        ),
    ] = None,
    no_concurrently: Annotated[
        bool,
        typer.Option(
            "--no-concurrently",
            help="Refresh with a plain REFRESH, which blocks readers while it runs.",
        ),
    ] = False,
    output_format: Annotated[str, typer.Option("--format", help="table or json.")] = "table",
    lock_timeout: Annotated[
        float | None,
        typer.Option(
            "--lock-timeout",
            min=0.001,
            help="Seconds to wait for another refresh or close. "
            "Default: the EQUS_LOCK_TIMEOUT_SECONDS setting.",
        ),
    ] = None,
) -> None:
    """Bring gold.v_resumen_equ (column H of RESUMEN EQU) up to date.

    Refreshes the two conversion tables Gold reads and their statistics, under
    a lock so that two refreshes never overlap; with --with-silver, Silver is
    built from Bronze first. A closed month is never touched.
    """
    try:
        output = _choice(output_format, ("table", "json"), "--format")
        wanted = parse_month(month) if month else None
        outcome = refresh_gold(
            with_silver=with_silver, concurrently=not no_concurrently, lock_timeout=lock_timeout
        )
        started = time.perf_counter()
        shown = wanted or latest_production_month()
        report, rows = resumen_equ(shown) if shown else ({}, [])
        reading = time.perf_counter() - started
    except EnergyError as error:
        raise _stop(error) from error

    steps = [
        {"paso": step.name, "segundos": round(step.seconds, 2), "detalle": step.detail}
        for step in outcome.steps
    ] + [{"paso": "compute column H", "segundos": round(reading, 2), "detalle": ""}]
    if output == "json":
        extra = {"pasos": steps}
        if shown:
            _print_column_h(report, rows, "json", extra=extra)
        else:
            typer.echo(json.dumps(extra | {"resumen": None}, ensure_ascii=False, indent=2))
        return

    if outcome.silver_report is not None:
        for line in describe(outcome.silver_report):
            typer.echo(line)
        typer.echo("")
    typer.echo("Refresh steps")
    for step in steps:
        detail = f"  ({step['detalle']})" if step["detalle"] else ""
        typer.echo(f"  {step['paso']:<34} {step['segundos']:>8.2f} s{detail}")
    typer.echo("")
    if not shown:
        typer.echo("Silver holds no production yet, so there is no column H to show.")
        return
    _print_column_h(report, rows, "table")


def equs_resumen_equ(
    month: Annotated[
        str | None, typer.Option("--month", help="The month, as YYYY-MM. Required.")
    ] = None,
    output_format: Annotated[str, typer.Option("--format", help="table, json or csv.")] = "table",
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help="live (computed now) or closed (the frozen close; fails if the month "
            "is not closed).",
        ),
    ] = "live",
) -> None:
    """Print column H of RESUMEN EQU for one month: every driver, then the totals.

    Read-only. One row per cuenta STATS in the order of the workbook, with its
    quantity (column D), factor (column G) and EQ (column H); then EQUS BATT,
    each plant and process (rows 36 to 45), and EQUS POLY.
    """
    try:
        if not month:
            raise InvalidPeriodError("--month is required, as YYYY-MM")
        output = _choice(output_format, _TABLE_FORMATS, "--format")
        report, rows = resumen_equ(parse_month(month), source=_source(source))
    except EnergyError as error:
        raise _stop(error) from error
    _print_column_h(report, rows, output)


def _print_quality(quality: Sequence[QualityCount]) -> None:
    for line in describe_quality(quality):
        typer.echo(line)
    typer.echo("")


def equs_close_month(
    month: Annotated[
        str | None, typer.Argument(help="The month to close, as YYYY-MM.", show_default=False)
    ] = None,
    accept_warnings: Annotated[
        bool,
        typer.Option(
            "--accept-warnings",
            help="Close even when a blocking data-quality check has rows; the decision "
            "is recorded with the close.",
        ),
    ] = False,
    reclose: Annotated[
        bool,
        typer.Option(
            "--reclose",
            help="Replace the month's close. The rows it replaces are kept in the history.",
        ),
    ] = False,
    closed_by: Annotated[str, typer.Option("--closed-by", help="Who closes the month.")] = "system",
    lock_timeout: Annotated[
        float | None,
        typer.Option(
            "--lock-timeout",
            min=0.001,
            help="Seconds to wait for another refresh or close. "
            "Default: the EQUS_LOCK_TIMEOUT_SECONDS setting.",
        ),
    ] = None,
) -> None:
    """Freeze a month's equivalent batteries as its official close.

    Refreshes the conversion tables, runs every data-quality check for the
    month, and stops if a blocking one has rows. Then the month's column H is
    copied into gold.resumen_equ in one transaction, as the workbook's
    "ESTA SE COPIA" column was. A closed month is never recomputed: only
    --reclose replaces it.
    """
    try:
        if not month:
            raise InvalidPeriodError("the month to close is required, as YYYY-MM")
        summary = close_month(
            parse_month(month),
            closed_by=closed_by,
            accept_warnings=accept_warnings,
            reclose=reclose,
            lock_timeout=lock_timeout,
            on_quality=_print_quality,
        )
    except EnergyError as error:
        raise _stop(error) from error
    for line in describe_close(summary):
        typer.echo(line)


def equs_report(
    start: Annotated[
        str | None, typer.Option("--start", help="First day, YYYY-MM-DD, included. Required.")
    ] = None,
    end: Annotated[
        str | None, typer.Option("--end", help="Last day, YYYY-MM-DD, included. Required.")
    ] = None,
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help="live (computed now) or closed (the frozen close of whole, closed months).",
        ),
    ] = "live",
    by_month: Annotated[
        bool, typer.Option("--by-month", help="Add a breakdown by month inside each subsection.")
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option("--output", dir_okay=False, help="Write the JSON here instead of stdout."),
    ] = None,
    dsn: Annotated[
        str | None,
        typer.Option(
            "--dsn",
            help="Database URL, postgresql://user:password@host:port/name. "
            "Default: the configured database.",
        ),
    ] = None,
) -> None:
    """Report the equivalent batteries of any date range as JSON, laid out like RESUMEN EQU.

    Both dates are included and compared with the accounting date of each
    production receipt and invoice line. Logs go to standard error.
    """
    try:
        if not start or not end:
            raise InvalidPeriodError("--start and --end are both required, as YYYY-MM-DD")
        report = report_for(start, end, _source(source), by_month, dsn=dsn)
    except EnergyError as error:
        raise _stop(error) from error

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if output is None:
        typer.echo(text)
        return
    output.write_text(text + "\n", encoding="utf-8")
    typer.echo(f"Report written to {output}.", err=True)


def _series_period(month: str | None, start: str | None, end: str | None) -> DateRange:
    """Read the range of ``equs daily``: a month, or a start and an end."""
    if month and (start or end):
        raise InvalidPeriodError("give --month, or --start and --end, not both")
    if month:
        return DateRange.of_month(parse_month(month))
    if not start or not end:
        raise InvalidPeriodError("give --month YYYY-MM, or both --start and --end as YYYY-MM-DD")
    return DateRange(parse_day(start, name="--start"), parse_day(end, name="--end"))


def equs_daily(
    month: Annotated[str | None, typer.Option("--month", help="A whole month, as YYYY-MM.")] = None,
    start: Annotated[
        str | None, typer.Option("--start", help="First day, YYYY-MM-DD, included.")
    ] = None,
    end: Annotated[
        str | None, typer.Option("--end", help="Last day, YYYY-MM-DD, included.")
    ] = None,
    frequency: Annotated[str, typer.Option("--frequency", help="day or month.")] = "day",
    process: Annotated[
        list[str] | None,
        typer.Option(
            "--process",
            help="Keep only this battery process (OREC, ENSAMBLE, FORMACION, DECORADO, "
            "SHIPPING). Repeat for several.",
        ),
    ] = None,
    area: Annotated[
        list[str] | None,
        typer.Option(
            "--area",
            help="Keep only the processes of this plant area (OREC, Ensamble, F&A). "
            "Repeat for several. SHIPPING has no area.",
        ),
    ] = None,
    output_format: Annotated[str, typer.Option("--format", help="table, json or csv.")] = "table",
) -> None:
    """Print the equivalent batteries of EQUS BATT day by day (or month by month).

    Read-only and live. Only the drivers that add to EQUS BATT: OREC, ENSAMBLE,
    FORMACION, DECORADO and SHIPPING. A day belongs to its accounting date
    (Fecha Efectiva) and uses the version of the equation in force in its
    month. By day, every whole month of the range is also checked: its days
    must add up to gold.v_resumen_equ exactly.
    """
    try:
        period = _series_period(month, start, end)
        chosen = _choice(frequency, FREQUENCIES, "--frequency")
        output = _choice(output_format, _TABLE_FORMATS, "--format")
        series = battery_equivalents(
            period,
            frequency="month" if chosen == "month" else "day",
            procesos=process or (),
            areas=area or (),
        )
    except EnergyError as error:
        raise _stop(error) from error

    if output == "json":
        typer.echo(json.dumps(series_json(series), ensure_ascii=False, indent=2))
    elif output == "csv":
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="\n").writerows(series_csv_rows(series))
        typer.echo(buffer.getvalue(), nl=False)
    else:
        for line in describe_series(series):
            typer.echo(line)
        for partial in series.period.partial_months if series.frequency == "month" else ():
            typer.secho(
                f"warning: {month_label(partial)} is only partly in the range",
                err=True,
                fg=typer.colors.YELLOW,
            )
    if series.mismatches:
        typer.secho(
            "error: the days do not add up to gold.v_resumen_equ", err=True, fg=typer.colors.RED
        )
        raise typer.Exit(code=EXIT_INVALID)


def equs_cruce(
    start: Annotated[
        str | None, typer.Option("--start", help="First day, YYYY-MM-DD, included.")
    ] = None,
    end: Annotated[
        str | None, typer.Option("--end", help="Last day, YYYY-MM-DD, included.")
    ] = None,
    frequency: Annotated[str, typer.Option("--frequency", help="day or month.")] = "day",
    area: Annotated[
        list[str] | None,
        typer.Option(
            "--area",
            help="Keep only this plant area (F&A, OREC, Ensamble). Repeat for several. "
            "Only F&A has daily energy.",
        ),
    ] = None,
    data_dir: Annotated[
        Path,
        typer.Option(
            "--data-dir",
            help="Directory holding the SCADA exports.",
            file_okay=False,
            dir_okay=True,
        ),
    ] = DEFAULT_DATA_DIRECTORY,
    output_format: Annotated[str, typer.Option("--format", help="table, json or csv.")] = "table",
) -> None:
    """Print the kWh of each plant area against its equivalent batteries.

    Read-only: the SCADA exports and Base_de_datos.xlsx are read from the data
    directory and the EQ from the database, live. F&A sets its three
    rectifiers against FORMACION + DECORADO, and takes the workbook for the
    months SCADA does not cover whole; OREC and Ensamble take the workbook, by
    month only. Every row says its energy source. A day is a local day; a
    frozen counter leaves its days without kWh but keeps its energy in the
    month and the range. kWh/EQ is always the ratio of the sums, over the
    months with energy. Exits 1, after printing, when the daily EQ does not add
    up to its months.
    """
    try:
        if not start or not end:
            raise InvalidPeriodError("give both --start and --end as YYYY-MM-DD")
        period = DateRange(parse_day(start, name="--start"), parse_day(end, name="--end"))
        chosen = _choice(frequency, FREQUENCIES, "--frequency")
        output = _choice(output_format, _TABLE_FORMATS, "--format")
        series = energy_cross(
            period,
            frequency="month" if chosen == "month" else "day",
            areas=area or (),
            data_dir=data_dir,
        )
    except SourceUnavailableError as error:
        # Usually the folder is not where the command runs: the api container has
        # no data/ of its own, only what a command mounts.
        stop = _stop(error)
        typer.secho(
            f"hint: the workbooks are read from --data-dir, here {data_dir.resolve()}. "
            "Inside Docker run `make cruce`, which mounts data/ read-only; with "
            "`docker compose run`, mount the folder with -v and pass its path as --data-dir.",
            err=True,
        )
        raise stop from error
    except EnergyError as error:
        raise _stop(error) from error

    if output == "json":
        typer.echo(json.dumps(cross_json(series), ensure_ascii=False, indent=2))
    elif output == "csv":
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="\n").writerows(cross_csv_rows(series))
        typer.echo(buffer.getvalue(), nl=False)
    else:
        for line in describe_cross(series):
            typer.echo(line)
    if series.eq_mismatches:
        typer.secho(
            "error: the daily EQ does not add up to gold.v_resumen_equ",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=EXIT_INVALID)


def build_app() -> typer.Typer:
    """Build the Typer application with every implemented command.

    Returns:
        The application. ``energy equs load`` lands one workbook,
        ``energy equs load-all`` lands a directory of them,
        ``energy equs build-silver`` builds Silver from what was landed,
        ``energy equs refresh-gold`` brings Gold up to date,
        ``energy equs resumen-equ`` prints column H of a month,
        ``energy equs close-month`` freezes a month,
        ``energy equs daily`` prints the EQ of EQUS BATT day by day and
        ``energy equs cruce`` sets the SCADA energy against the EQ.
    """
    app = typer.Typer(
        name="energy",
        help="Operational commands for the Clarios Energy platform.",
        no_args_is_help=True,
        add_completion=False,
    )
    equs = typer.Typer(
        help=(
            "Equivalent-batteries (EQUS) data: land the source workbooks in Bronze, "
            "build Silver from them, and refresh, read and close Gold."
        ),
        no_args_is_help=True,
    )
    equs.command("load")(equs_load)
    equs.command("load-all")(equs_load_all)
    equs.command("build-silver")(equs_build_silver)
    equs.command("refresh-gold")(equs_refresh_gold)
    equs.command("resumen-equ")(equs_resumen_equ)
    equs.command("close-month")(equs_close_month)
    equs.command("daily")(equs_daily)
    equs.command("cruce")(equs_cruce)
    app.add_typer(equs, name="equs")
    return app


def build_report_app() -> typer.Typer:
    """Build the application of the ``equs-report`` command."""
    app = typer.Typer(name="equs-report", add_completion=False)
    app.command()(equs_report)
    return app


def _run(app: typer.Typer) -> None:
    """Run an application, with Click's usage errors exiting 1 instead of 2.

    Exit code 2 belongs to database errors in the EQUS Gold commands, so a
    malformed command line must not use it.
    """
    try:
        result = app(standalone_mode=False)
    except typer.Abort:
        typer.echo("Aborted.", err=True)
        raise SystemExit(EXIT_INVALID) from None
    except typer.TyperException as error:  # a usage error of the command line
        show = getattr(error, "show", None)
        if callable(show):
            show()
        code = getattr(error, "exit_code", EXIT_INVALID)
        raise SystemExit(EXIT_INVALID if code == _CLICK_USAGE_ERROR else code) from None
    raise SystemExit(result if isinstance(result, int) else 0)


def main() -> None:
    """Build the Typer application and dispatch the requested command."""
    configure_from_settings(stream=sys.stderr)
    _run(build_app())


def report_main() -> None:
    """Run ``equs-report``: the JSON goes to standard output, logs to standard error."""
    configure_from_settings(stream=sys.stderr)
    _run(build_report_app())


if __name__ == "__main__":
    main()
