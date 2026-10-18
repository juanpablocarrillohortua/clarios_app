"""Cross the energy of the plant's exports with the equivalent batteries (EQ).

Layer: pipelines

Responsibility:
    The composition root of the energy-EQ cross. It knows what the service
    does not: which export in ``data/`` holds the meters of which plant area,
    under which headers, in which unit and time zone. It wires those exports
    and the EQ of the database (read through
    :class:`~energy.services.equs_gold.BatterySeries`) into
    :class:`~energy.services.energy_cross.EnergyCross`.

    The sources, in priority order:

    1. the SCADA counters of F&A's three rectifiers, by day;
    2. ``Base_de_datos.xlsx``, by month, for OREC, Ensamble and F&A.

    So F&A takes SCADA for every month SCADA covers whole (from August 2026)
    and the workbook for the others; OREC and Ensamble take the workbook.
    SHIPPING has no energy at all.

    RC has energy (SCADA and the workbook) but no EQ, so it is not a source of
    the cross. :func:`rc_sources` reads it on its own, for comparisons such as
    what the main meter EMCALI measures. That comparison found that EMCALI is
    neither RC alone nor the whole site, so it gives no daily energy for OREC
    and Ensamble (``notebooks/eda_cruce_herramienta.ipynb``).

    Nothing here writes: the workbooks are read and the database is queried.

Depends on:
    ``energy.services``, ``energy.adapters``, ``energy.db.session``,
    ``energy.domain``, ``energy.core``.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from energy.adapters.energy_book import BookSheet, EnergyBookSource
from energy.adapters.postgres_equs_gold import PostgresGoldReader
from energy.adapters.scada_workbook import CounterWorkbook, ScadaCounterSource
from energy.core.settings import get_settings
from energy.db.session import get_engine
from energy.services.energy_cross import EnergyCross
from energy.services.equs_gold import BatterySeries

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.engine import Engine

    from energy.domain.energy_cross import EnergyCrossSeries
    from energy.domain.equs_gold import DateRange
    from energy.domain.equs_series import Frequency
    from energy.ports.energy_cross import AreaEnergySource

DEFAULT_DATA_DIRECTORY: Final = Path("data")

# The SCADA exports write naive local time; the plant is in Colombia (UTC-5, no DST).
SCADA_ZONE: Final = ZoneInfo("America/Bogota")

# F&A: the counters of the three formation rectifiers, in Wh.
FA_AREA: Final = "F&A"
FA_WORKBOOK: Final = "SCADA-Base de datos F&A.xlsx"
SCADA_SHEET: Final = "Dataset 1"
SCADA_TIMESTAMP: Final = "t_stamp"
FA_METERS: Final = ("RECTIFICADOR 1", "RECTIFICADOR 2", "RECTIFICADOR 3")

# Drivers that describe an area without adding to its EQ. The EQ data files them
# under no area: the formed batteries, by type, are F&A's (HFM calls their sum
# 15BATPRD). LEADPROD is RC's lead and stays out of the cross.
INFORMATIVE_DRIVERS: Final = {FA_AREA: ("FORM_PRD_SLI", "FORM_PRD_TRUCK")}

# The monthly workbook: each area's total row, named as the EQ data names the area.
ENERGY_BOOK: Final = "Base_de_datos.xlsx"
BOOK_SHEETS: Final = (
    BookSheet(area="OREC", sheet="OREC", total_row="OREC"),
    BookSheet(area="Ensamble", sheet="Ensamble", total_row="Ensamble"),
    BookSheet(area=FA_AREA, sheet="F&A", total_row="F&A"),
)

# RC, outside the cross: the plant's SCADA meters and its sheet of the workbook.
# EMCALI, the main meter, counts kWh; the other meters count Wh.
RC_AREA: Final = "RC"
RC_WORKBOOK: Final = "SCADA-Base de datos Energia RC.xlsx"
RC_MAIN_METER: Final = "EMCALI"
RC_BUILDING_METER: Final = "EDIF ADMON"
RC_PLANT_METERS: Final = (
    "FILTRO 1",
    "FILTRO 2",
    "HORNO",
    "REFINO 1",
    "REFINO 2",
    "TRITURADO",
    "PTARI",
)
RC_BOOK_SHEET: Final = BookSheet(area=RC_AREA, sheet="RC Energia", total_row="RC")


def energy_sources(data_dir: Path | None = None) -> tuple[AreaEnergySource, ...]:
    """Return the energy sources of the cross, in priority order.

    Args:
        data_dir: the directory holding the exports; ``data/`` when ``None``.

    Returns:
        The SCADA counters first, then the monthly workbook.
    """
    directory = data_dir or DEFAULT_DATA_DIRECTORY
    return (
        ScadaCounterSource(
            (
                CounterWorkbook(
                    area=FA_AREA,
                    path=directory / FA_WORKBOOK,
                    sheet=SCADA_SHEET,
                    timestamp_header=SCADA_TIMESTAMP,
                    meters=FA_METERS,
                    zone=SCADA_ZONE,
                ),
            )
        ),
        EnergyBookSource(directory / ENERGY_BOOK, BOOK_SHEETS),
    )


def rc_sources(data_dir: Path | None = None) -> tuple[ScadaCounterSource, EnergyBookSource]:
    """Return the energy of RC, which has no EQ: its SCADA meters and its sheet.

    The SCADA source has every meter of the export, the main meter EMCALI and
    the administration building included, as the area ``RC``; read them one by
    one with :func:`~energy.domain.energy_cross.meter_months`.

    Args:
        data_dir: the directory holding the exports; ``data/`` when ``None``.

    Returns:
        The SCADA counters and the monthly workbook, each for ``RC`` alone.
    """
    directory = data_dir or DEFAULT_DATA_DIRECTORY
    scada = ScadaCounterSource(
        (
            CounterWorkbook(
                area=RC_AREA,
                path=directory / RC_WORKBOOK,
                sheet=SCADA_SHEET,
                timestamp_header=SCADA_TIMESTAMP,
                meters=(RC_MAIN_METER, RC_BUILDING_METER, *RC_PLANT_METERS),
                zone=SCADA_ZONE,
                meter_units_per_kwh={RC_MAIN_METER: Decimal(1)},
            ),
        )
    )
    return scada, EnergyBookSource(directory / ENERGY_BOOK, (RC_BOOK_SHEET,))


def build_cross(*, data_dir: Path | None = None, engine: Engine | None = None) -> EnergyCross:
    """Wire the cross to the exports and the database.

    Build it once and call :meth:`~energy.services.energy_cross.EnergyCross.cross`
    as often as needed: each export is read once per cross object.

    Args:
        data_dir: the directory holding the exports; ``data/`` when ``None``.
        engine: the database engine; the process-wide one when ``None``.

    Returns:
        The service.
    """
    reader = PostgresGoldReader(
        engine or get_engine(), variation_threshold=get_settings().equs.variation_threshold
    )
    return EnergyCross(energy_sources(data_dir), BatterySeries(reader, reader), INFORMATIVE_DRIVERS)


def energy_cross(
    period: DateRange,
    *,
    frequency: Frequency = "day",
    areas: Sequence[str] = (),
    data_dir: Path | None = None,
    engine: Engine | None = None,
) -> EnergyCrossSeries:
    """Set the energy of each area against its EQ over a range.

    Args:
        period: the days, both ends included.
        frequency: ``day`` or ``month``.
        areas: the areas to cross; every area with energy when empty.
        data_dir: the directory holding the exports; ``data/`` when ``None``.
        engine: the database engine; the process-wide one when ``None``.

    Returns:
        The cross of each area.

    Raises:
        InvalidPeriodError: if the range, the frequency or an area cannot be answered.
        SourceUnavailableError: if an export cannot be read.
        RepositoryError: if the database fails.
    """
    return build_cross(data_dir=data_dir, engine=engine).cross(
        period, frequency=frequency, areas=areas
    )


__all__ = [
    "DEFAULT_DATA_DIRECTORY",
    "INFORMATIVE_DRIVERS",
    "SCADA_ZONE",
    "build_cross",
    "energy_cross",
    "energy_sources",
    "rc_sources",
]
