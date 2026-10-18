"""Cross the energy of the SCADA exports with the equivalent batteries (EQ).

Layer: pipelines

Responsibility:
    The composition root of the energy-EQ cross. It knows what the service
    does not: which export in ``data/`` holds the meters of which plant area,
    under which headers, in which unit and time zone. It wires those exports
    and the EQ of the database (read through
    :class:`~energy.services.equs_gold.BatterySeries`) into
    :class:`~energy.services.energy_cross.EnergyCross`.

    Only F&A is crossed: its three rectifiers are the only meters with an EQ
    to set them against. RC has SCADA energy and gas but no EQ, so it has no
    export here. A monthly source for OREC and ENSAMBLE would be one more entry
    of :func:`energy_sources`.

    Nothing here writes: the workbooks are read and the database is queried.

Depends on:
    ``energy.services``, ``energy.adapters``, ``energy.db.session``,
    ``energy.domain``, ``energy.core``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

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
FA_SHEET: Final = "Dataset 1"
FA_TIMESTAMP: Final = "t_stamp"
FA_METERS: Final = ("RECTIFICADOR 1", "RECTIFICADOR 2", "RECTIFICADOR 3")


def energy_sources(data_dir: Path | None = None) -> tuple[AreaEnergySource, ...]:
    """Return the energy sources of the cross, reading from a directory.

    Args:
        data_dir: the directory holding the exports; ``data/`` when ``None``.

    Returns:
        The sources, one per kind of export.
    """
    directory = data_dir or DEFAULT_DATA_DIRECTORY
    return (
        ScadaCounterSource(
            (
                CounterWorkbook(
                    area=FA_AREA,
                    path=directory / FA_WORKBOOK,
                    sheet=FA_SHEET,
                    timestamp_header=FA_TIMESTAMP,
                    meters=FA_METERS,
                    zone=SCADA_ZONE,
                ),
            )
        ),
    )


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
    return EnergyCross(energy_sources(data_dir), BatterySeries(reader, reader))


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
    "SCADA_ZONE",
    "build_cross",
    "energy_cross",
    "energy_sources",
]
