"""Ports of the energy-EQ cross: where the energy of an area and its EQ come from.

Layer: ports

Responsibility:
    Declares the two things the cross reads, so that the service never knows
    whether an area's energy comes from a SCADA export, a monthly workbook or,
    later, the database.

    :class:`AreaEnergySource`
        Answers the energy of the plant areas it knows, over a range. A source
        with daily data (counters) answers by day and by month; one with only
        monthly data answers by month and says so through
        :attr:`~energy.domain.energy_cross.AreaEnergy.resolution`. Adding the
        monthly workbook of OREC and ENSAMBLE is one more source of this kind;
        the service does not change.

    :class:`EquivalentsReader`
        Answers the equivalent batteries of the battery processes over a range.
        :class:`energy.services.equs_gold.BatterySeries` satisfies it as it is.

Depends on:
    ``energy.domain``, standard library typing only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from energy.domain.energy_cross import AreaEnergy
    from energy.domain.equs_gold import DateRange
    from energy.domain.equs_series import BatteryEquivalents, Frequency


@runtime_checkable
class AreaEnergySource(Protocol):
    """The energy of some plant areas."""

    @property
    def areas(self) -> tuple[str, ...]:
        """Return the plant areas this source has energy for, as the EQ data names them."""
        ...

    def energy(self, area: str, period: DateRange) -> AreaEnergy:
        """Return the energy of one area over a range.

        Args:
            area: one of :attr:`areas`.
            period: the days, both ends included.

        Returns:
            The energy by day (when the source has days), by month clipped to the
            range, and over the whole range. A day or month the source cannot
            answer has no kWh; it is never 0.

        Raises:
            SourceUnavailableError: if the source cannot be read.
            UnsupportedFileFormatError: if the source is not what it should be.
        """
        ...


@runtime_checkable
class EquivalentsReader(Protocol):
    """The equivalent batteries of the battery processes."""

    def series(
        self,
        period: DateRange,
        *,
        frequency: Frequency = "day",
        procesos: Sequence[str] = (),
        areas: Sequence[str] = (),
    ) -> BatteryEquivalents:
        """Return the EQ of the battery processes over a range.

        Args:
            period: the days, both ends included.
            frequency: ``day`` or ``month``.
            procesos: keep only these battery processes; all when empty.
            areas: keep only the processes of these plant areas; all when empty.

        Returns:
            The series; a daily one carries the check of its whole months.

        Raises:
            InvalidPeriodError: if the range or a filter cannot be answered.
            RepositoryError: if the store fails.
        """
        ...


__all__ = ["AreaEnergySource", "EquivalentsReader"]
