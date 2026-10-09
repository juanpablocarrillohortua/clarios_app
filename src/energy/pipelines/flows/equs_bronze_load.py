"""Land the equivalent-batteries (EQUS) workbooks in Bronze.

Layer: pipelines

Responsibility:
    The composition root of the Bronze load. It builds the
    :class:`~energy.services.bronze_loader.BronzeLoader` from its adapters, and
    it knows the one thing the service does not: which workbook of the monthly
    close feeds which source, with which sheets, and in what order. Loading
    "everything" is that routing table applied to a directory. File names are
    matched tolerantly, because the same export arrives with or without a space
    before a hyphen, and a missing workbook is reported without stopping the
    others.

    Nothing here knows about a year. The two movement exports - production
    receipts and invoices - may be delivered several times, one file per period,
    side by side under the usual name followed by anything
    (``... por art FY24.xlsx``); every one of them is landed, each under its own
    run. Product structures and catalogues are snapshots, where the latest load
    is the one that counts, so those stay one file per exact name. A workbook
    that no route claims is reported rather than passed over in silence.

    These are plain functions rather than Prefect flows on purpose: the load
    has to run from a shell on a machine with no Prefect server. A scheduled
    flow can wrap :func:`load_all` unchanged.

Depends on:
    ``energy.services.bronze_loader``, ``energy.adapters``, ``energy.db.session``,
    ``energy.domain``, ``energy.core``.
"""

from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Final, Literal

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.adapters.local_storage import LocalObjectStorage
from energy.adapters.minio_storage import MinioObjectStorage
from energy.adapters.postgres_bronze_repo import PostgresBronzeLandingRepository
from energy.core.logging import get_logger
from energy.core.settings import get_settings
from energy.db.session import get_engine
from energy.domain.exceptions import EnergyError, SourceUnavailableError
from energy.domain.landing import DEFAULT_PLANT_CODE, SheetSpec
from energy.services.bronze_loader import BronzeLoader, LoadRequest

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date

    from sqlalchemy.engine import Engine

    from energy.ports.object_storage import ObjectStorage
    from energy.services.bronze_loader import LoadResult

logger = get_logger(__name__)

DEFAULT_DIRECTORY: Final = Path("data/raw_excels")

_WORKBOOK_SUFFIXES: Final = frozenset({".xlsx", ".xlsm", ".xls"})
# Excel leaves a "~$name.xlsx" lock file beside a workbook that is open.
_LOCK_FILE_PREFIX: Final = "~$"

RouteStatus = Literal["landed", "skipped", "missing", "failed"]


@dataclass(frozen=True, slots=True)
class WorkbookRoute:
    """One load of the monthly close: a workbook, a source and its sheets.

    Attributes:
        source_key: the source the workbook is landed for.
        filename: the expected file name; matched ignoring case, spaces, hyphens
            and accents.
        sheets: the sheets to land. Empty means the source's default sheets.
        optional_sheets: sheets landed only on request, because they are useful
            but not clean.
        many: whether the export may be delivered several times, one file per
            period. Every workbook whose name starts with ``filename`` is then
            landed; otherwise only the one with exactly that name.
        optional: whether the workbook may be absent without that being an
            error.
    """

    source_key: str
    filename: str
    sheets: tuple[SheetSpec, ...] = ()
    optional_sheets: tuple[SheetSpec, ...] = ()
    many: bool = False
    optional: bool = False


# In load order: movements first, then the product structures, then the
# hand-maintained catalogues, and the item master when it exists. No file name
# here is the beginning of another, so a workbook can only ever follow one of
# the two routes that take several files.
ROUTES: Final[tuple[WorkbookRoute, ...]] = (
    WorkbookRoute(
        "qad_rct_wo",
        "Calculo de equivalente - Explorar transacciones por art.xlsx",
        many=True,
    ),
    WorkbookRoute(
        "qad_facturas",
        "Calculo de equivalente - Unidades facturadas.xlsx",
        many=True,
    ),
    WorkbookRoute(
        "qad_product_struc",
        "BOM BATERIAS FORMADAS.xlsx",
        sheets=(
            SheetSpec("PASTA"),
            SheetSpec("PLACAS"),
            SheetSpec("BAT ENS"),
            SheetSpec("BAT FOR"),
            SheetSpec("BAT TERM"),
        ),
    ),
    WorkbookRoute("qad_product_struc", "ESTRUCTURA DECORADO.xlsx", sheets=(SheetSpec("Datos"),)),
    WorkbookRoute(
        "manual_sheets",
        "EQUS BATT.xlsx",
        sheets=(
            SheetSpec("Clasif_Poly", "clasif_poly"),
            SheetSpec("Lineas Produccion", "lineas_produccion"),
            # INSTRUCC is a list of notes with no header row.
            SheetSpec("INSTRUCC", "instrucc", header_row=0),
        ),
        # Gives the GRUPO of each production line, but shares its sheet with two
        # pivot tables, so it is landed only when asked for.
        optional_sheets=(SheetSpec("L.PRODUCCION", "l_produccion", header_row=3),),
    ),
    WorkbookRoute(
        "manual_sheets",
        "ESTRUCTURA DECORADO.xlsx",
        sheets=(SheetSpec("ELEMENT DECO", "element_deco"),),
    ),
    WorkbookRoute(
        "manual_sheets",
        "Calculo de equivalente- Comparativo lineas.xlsx",
        sheets=(SheetSpec("Hoja1", "comparativo_lineas"),),
    ),
    # Not delivered yet; loaded as soon as the file appears.
    WorkbookRoute("item_master", "Item_Master.xlsx", optional=True),
)


@dataclass(frozen=True, slots=True)
class RouteOutcome:
    """What happened to one load of a load-all.

    A route gives one outcome per workbook it found, and a single ``missing``
    outcome when it found none.

    Attributes:
        route: the route.
        status: ``landed``, ``skipped`` (everything was already landed),
            ``missing`` (no such workbook) or ``failed``.
        path: the workbook that was loaded; ``None`` when it is missing.
        result: the load result; ``None`` when the route is missing or failed.
        error: why the route failed; empty otherwise.
        seconds: how long the route took.
    """

    route: WorkbookRoute
    status: RouteStatus
    path: Path | None = None
    result: LoadResult | None = None
    error: str = ""
    seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class LoadAllResult:
    """The outcome of loading a whole directory.

    Attributes:
        outcomes: one outcome per load, in load order.
        seconds: how long the whole load took.
        unmatched: the workbooks of the directory that no route claimed, and
            that were therefore not loaded.
    """

    outcomes: tuple[RouteOutcome, ...]
    seconds: float
    unmatched: tuple[Path, ...] = ()

    @property
    def rows_landed(self) -> int:
        """Return how many rows the load landed."""
        return sum(o.result.rows_landed for o in self.outcomes if o.result is not None)

    @property
    def ok(self) -> bool:
        """Return whether no route failed and no required workbook is missing."""
        return not any(
            outcome.status == "failed"
            or (outcome.status == "missing" and not outcome.route.optional)
            for outcome in self.outcomes
        )


def _normalised(filename: str) -> str:
    """Reduce a file name to its letters and digits, lowercase and unaccented."""
    stem = unicodedata.normalize("NFKD", PurePath(filename).stem)
    return "".join(character for character in stem.casefold() if character.isalnum())


def _workbooks(directory: Path) -> list[Path]:
    """List the workbooks of a directory, without Excel's lock files.

    They come ordered by what their names say, so a name that only adds
    something to another follows it: ``... por art.xlsx`` before
    ``... por art FY24.xlsx``.
    """
    if not directory.is_dir():
        return []
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix.lower() in _WORKBOOK_SUFFIXES
            and not path.name.startswith(_LOCK_FILE_PREFIX)
        ),
        key=lambda path: (_normalised(path.name), path.name),
    )


def _matching(workbooks: Sequence[Path], filename: str, many: bool = False) -> tuple[Path, ...]:
    """Pick the workbooks that answer to a file name out of a directory listing.

    With ``many``, every workbook whose name starts with ``filename``; otherwise
    the one whose name is ``filename``, both compared by letters and digits.
    """
    wanted = _normalised(filename)
    if many:
        return tuple(path for path in workbooks if _normalised(path.name).startswith(wanted))

    matches = [path for path in workbooks if _normalised(path.name) == wanted]
    if len(matches) > 1:
        logger.warning(
            "several workbooks match; using the first",
            wanted=filename,
            matches=[path.name for path in matches],
        )
    return tuple(matches[:1])


def find_workbook(directory: Path, filename: str) -> Path | None:
    """Find a workbook by name, tolerating how the name was typed.

    Args:
        directory: where to look; not searched recursively.
        filename: the expected file name.

    Returns:
        The matching workbook, or ``None``. Two names match when they agree on
        their letters and digits, so ``equivalente - Explorar`` finds
        ``equivalente- Explorar``. Excel's lock files are ignored.
    """
    found = _matching(_workbooks(directory), filename)
    return found[0] if found else None


def find_workbooks(directory: Path, route: WorkbookRoute) -> tuple[Path, ...]:
    """Find every workbook a route lands.

    Args:
        directory: where to look; not searched recursively.
        route: the route.

    Returns:
        For a route that takes several files, every workbook whose name starts
        with the route's file name, the plain name first. Otherwise the one
        workbook :func:`find_workbook` finds, or nothing.
    """
    return _matching(_workbooks(directory), route.filename, route.many)


def build_storage(archive_dir: Path | None = None) -> ObjectStorage:
    """Build the object storage the originals are archived into.

    Args:
        archive_dir: archive into this directory instead of the configured
            backend.

    Returns:
        A directory-backed store when ``archive_dir`` is given or the configured
        backend is ``local``; the MinIO store otherwise.
    """
    settings = get_settings()
    if archive_dir is not None:
        return LocalObjectStorage(archive_dir)
    if settings.archive.backend == "local":
        return LocalObjectStorage(settings.archive.local_dir)
    return MinioObjectStorage(settings.minio)


def build_loader(engine: Engine | None = None, archive_dir: Path | None = None) -> BronzeLoader:
    """Wire the Bronze loader to its adapters.

    Args:
        engine: the database engine; the process-wide one when ``None``.
        archive_dir: archive originals into this directory instead of the
            configured backend.

    Returns:
        A ready :class:`~energy.services.bronze_loader.BronzeLoader`.
    """
    return BronzeLoader(
        reader=CalamineWorkbookReader(),
        storage=build_storage(archive_dir),
        repository=PostgresBronzeLandingRepository(engine or get_engine()),
        bucket=get_settings().minio.bucket_bronze,
    )


def load_workbook(
    loader: BronzeLoader,
    source_key: str,
    path: Path,
    sheets: Sequence[SheetSpec] = (),
    plant_code: str = DEFAULT_PLANT_CODE,
    flow_name: str | None = None,
    window_start: date | None = None,
    window_end: date | None = None,
    force: bool = False,
    uploaded_by: str = "system",
) -> LoadResult:
    """Land one workbook, or some of its sheets.

    Args:
        loader: the loader to use, from :func:`build_loader`.
        source_key: which kind of export the workbook is.
        path: the workbook file. It is only read, never modified.
        sheets: the sheets to land; empty for the source's defaults.
        plant_code: the plant the data belongs to.
        flow_name: the name the run is recorded under; the file stem by default.
        window_start: first date of the business period. Stating either bound
            replaces the period found in the rows.
        window_end: last date of the business period, likewise.
        force: land the sheets again even if a completed run already did.
        uploaded_by: who submitted the file.

    Returns:
        The :class:`~energy.services.bronze_loader.LoadResult`.

    Raises:
        SourceUnavailableError: if the file cannot be read.
        EnergyError: whatever the loader raises.
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SourceUnavailableError(
            "workbook cannot be read", path=str(path), reason=str(exc)
        ) from exc

    return loader.load(
        LoadRequest(
            source_key=source_key,
            filename=path.name,
            data=data,
            sheets=tuple(sheets),
            plant_code=plant_code,
            flow_name=flow_name,
            window_start=window_start,
            window_end=window_end,
            force=force,
            uploaded_by=uploaded_by,
        )
    )


def _load_route(
    loader: BronzeLoader,
    route: WorkbookRoute,
    path: Path,
    plant_code: str,
    force: bool,
    include_optional: bool,
    uploaded_by: str,
) -> RouteOutcome:
    """Land one workbook of a route and say how it went, without raising."""
    started = time.perf_counter()
    sheets = route.sheets + (route.optional_sheets if include_optional else ())
    try:
        result = load_workbook(
            loader,
            route.source_key,
            path,
            sheets,
            plant_code=plant_code,
            force=force,
            uploaded_by=uploaded_by,
        )
    except EnergyError as exc:
        logger.error("route failed", source_key=route.source_key, file=path.name, error=str(exc))
        return RouteOutcome(
            route, "failed", path=path, error=str(exc), seconds=time.perf_counter() - started
        )
    return RouteOutcome(
        route,
        "skipped" if result.skipped else "landed",
        path=path,
        result=result,
        seconds=time.perf_counter() - started,
    )


def load_all(
    loader: BronzeLoader,
    directory: Path = DEFAULT_DIRECTORY,
    plant_code: str = DEFAULT_PLANT_CODE,
    force: bool = False,
    include_optional: bool = False,
    uploaded_by: str = "system",
    on_outcome: Callable[[RouteOutcome], None] | None = None,
    routes: Sequence[WorkbookRoute] = ROUTES,
) -> LoadAllResult:
    """Land every workbook of the monthly close found in a directory.

    Each workbook a route finds is one load with its own run, whatever years it
    holds. A load that fails or a route whose workbook is missing is reported
    and the others still run.

    Args:
        loader: the loader to use, from :func:`build_loader`.
        directory: where the workbooks are.
        plant_code: the plant the data belongs to.
        force: land everything again even if completed runs already did.
        include_optional: also land the optional sheets of each route.
        uploaded_by: who submitted the files.
        on_outcome: called with each load's outcome as soon as it is known, so
            a caller can report progress.
        routes: the routing table to apply.

    Returns:
        A :class:`LoadAllResult` with one outcome per load, and the workbooks no
        route claimed.
    """
    started = time.perf_counter()
    workbooks = _workbooks(directory)
    outcomes: list[RouteOutcome] = []
    claimed: set[Path] = set()

    def report(outcome: RouteOutcome) -> None:
        outcomes.append(outcome)
        if on_outcome is not None:
            on_outcome(outcome)

    for route in routes:
        paths = _matching(workbooks, route.filename, route.many)
        claimed.update(paths)
        if not paths:
            report(RouteOutcome(route, "missing"))
        for path in paths:
            report(
                _load_route(loader, route, path, plant_code, force, include_optional, uploaded_by)
            )

    return LoadAllResult(
        tuple(outcomes),
        time.perf_counter() - started,
        unmatched=tuple(path for path in workbooks if path not in claimed),
    )


__all__ = [
    "DEFAULT_DIRECTORY",
    "ROUTES",
    "LoadAllResult",
    "RouteOutcome",
    "RouteStatus",
    "WorkbookRoute",
    "build_loader",
    "build_storage",
    "find_workbook",
    "find_workbooks",
    "load_all",
    "load_workbook",
]
