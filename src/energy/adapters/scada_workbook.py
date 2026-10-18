"""The energy of a plant area, read from a SCADA counter export.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.energy_cross.AreaEnergySource` over the
    workbooks the SCADA system exports: one sheet, a timestamp column and one
    cumulative counter column per meter. It reads the file as it is, read-only,
    and never writes anywhere.

    What it knows about the export is what the domain must not: the header
    texts, that the timestamps are naive local time, and the unit of the
    counters. It turns each row into an aware reading in kWh and hands
    everything else - intervals, frozen counters, days, months - to
    :mod:`energy.domain.energy_cross`. A file is read and classified once per
    source object.

Depends on:
    ``energy.ports``, ``energy.domain``, ``energy.adapters.calamine_workbook``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.domain.energy_cross import (
    AreaEnergy,
    CounterEnergy,
    CounterReading,
    classify_counters,
    counter_area_energy,
)
from energy.domain.exceptions import (
    ConfigurationError,
    InvalidPeriodError,
    SourceUnavailableError,
    UnsupportedFileFormatError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from datetime import tzinfo
    from pathlib import Path

    from energy.domain.equs_gold import DateRange
    from energy.domain.landing import CellValue
    from energy.ports.workbook_reader import WorkbookReader


@dataclass(frozen=True, slots=True)
class CounterWorkbook:
    """One SCADA export: the meters of one plant area.

    Attributes:
        area: the plant area, as the EQ data names it (``F&A``).
        path: the workbook.
        sheet: the sheet holding the readings.
        timestamp_header: the header of the timestamp column.
        meters: the headers of the counter columns, one per meter.
        zone: the time zone of the naive timestamps.
        units_per_kwh: how many counter units make one kWh (1000 for Wh).
        meter_units_per_kwh: the same, for the meters whose unit differs (the
            RC export's main meter counts kWh while the others count Wh).
    """

    area: str
    path: Path
    sheet: str
    timestamp_header: str
    meters: tuple[str, ...]
    zone: tzinfo
    units_per_kwh: Decimal = Decimal(1000)
    meter_units_per_kwh: Mapping[str, Decimal] = field(default_factory=dict)

    def units(self, meter: str) -> Decimal:
        """Return how many counter units of one meter make one kWh."""
        return self.meter_units_per_kwh.get(meter, self.units_per_kwh)


def _counter(value: CellValue) -> Decimal | None:
    """A counter cell in its own unit; blank or text is no reading."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return Decimal(repr(value)) if isinstance(value, float) else Decimal(value)


class ScadaCounterSource:
    """The energy of the plant areas whose meters a SCADA export holds.

    Args:
        workbooks: one export per area.
        reader: the workbook reader; calamine when ``None``.

    Raises:
        ConfigurationError: if two exports claim the same area.
    """

    def __init__(
        self, workbooks: Sequence[CounterWorkbook], reader: WorkbookReader | None = None
    ) -> None:
        by_area: dict[str, CounterWorkbook] = {}
        for workbook in workbooks:
            if workbook.area in by_area:
                raise ConfigurationError("two SCADA exports for one area", area=workbook.area)
            by_area[workbook.area] = workbook
        self._workbooks = by_area
        self._reader = reader or CalamineWorkbookReader()
        self._counters: dict[str, CounterEnergy] = {}

    @property
    def areas(self) -> tuple[str, ...]:
        """Return the areas with an export, in the order given."""
        return tuple(self._workbooks)

    def energy(self, area: str, period: DateRange) -> AreaEnergy:
        """Return the energy of one area by day, by month and over the range.

        Raises:
            InvalidPeriodError: if this source has no export for the area.
            SourceUnavailableError: if the workbook cannot be read.
            UnsupportedFileFormatError: if a column is missing or a timestamp
                is not a date and time.
        """
        workbook = self._workbooks.get(area)
        if workbook is None:
            raise InvalidPeriodError("no SCADA export for this area", area=area, areas=self.areas)
        return counter_area_energy(area, workbook.path.name, self.counters(area), period)

    def counters(self, area: str) -> CounterEnergy:
        """Return the classified counters of one area, reading the export once."""
        if area not in self._counters:
            workbook = self._workbooks[area]
            self._counters[area] = classify_counters(
                workbook.meters, self._readings(workbook), workbook.zone
            )
        return self._counters[area]

    def _readings(self, workbook: CounterWorkbook) -> list[CounterReading]:
        try:
            data = workbook.path.read_bytes()
        except OSError as exc:
            raise SourceUnavailableError(
                "SCADA export cannot be read", path=str(workbook.path), reason=str(exc)
            ) from exc
        rows: Iterator[Sequence[CellValue]] = (
            self._reader.open_workbook(data).sheet(workbook.sheet).rows
        )
        header = [str(cell).strip() if cell is not None else "" for cell in next(rows, ())]
        wanted = (workbook.timestamp_header, *workbook.meters)
        missing = [name for name in wanted if name not in header]
        if missing:
            raise UnsupportedFileFormatError(
                "SCADA export lacks a column",
                path=str(workbook.path),
                missing=missing,
                header=header,
            )
        positions = [header.index(name) for name in wanted]

        readings: list[CounterReading] = []
        for number, row in enumerate(rows, start=2):
            cells = [row[position] if position < len(row) else None for position in positions]
            if all(cell in (None, "") for cell in cells):
                continue  # a blank row is no reading
            stamp = cells[0]
            if not isinstance(stamp, datetime):
                raise UnsupportedFileFormatError(
                    "SCADA timestamp is not a date and time",
                    path=str(workbook.path),
                    row=number,
                    value=str(stamp),
                )
            local = stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=workbook.zone)
            kwh = tuple(
                None if (value := _counter(cell)) is None else value / workbook.units(meter)
                for meter, cell in zip(workbook.meters, cells[1:], strict=True)
            )
            readings.append(CounterReading(local, kwh))
        return readings


__all__ = ["CounterWorkbook", "ScadaCounterSource"]
