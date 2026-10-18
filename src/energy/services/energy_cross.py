"""Application service of the energy-EQ cross.

Layer: services

Responsibility:
    :class:`EnergyCross` sets the energy of each plant area against the
    equivalent batteries (EQ) of the battery processes of that same area, by
    day or by month, over any range. It only reads: the energy from the
    :class:`~energy.ports.energy_cross.AreaEnergySource` objects it is given,
    the EQ from an :class:`~energy.ports.energy_cross.EquivalentsReader`.

    Which processes belong to an area is the EQ data's own answer (the plant
    area of each process in ``gold.secciones_reporte``), so nothing here names a
    process. An area is crossed only where a source has energy for it and the
    EQ data has processes for it; RC, for instance, has energy but no EQ, and
    no source is given for it.

    An area may have several sources, and the order the sources are given in
    is their priority: each row takes the first that has kWh for all of it
    (SCADA counters before the monthly workbook), and says which. A source
    with only monthly energy takes no part in the daily cross; an area with no
    source that has days is left out of it when every area is asked for, and
    refused when it is named.

    The EQ is always read by day and added up over the days of each row, so a
    month cut by the range gets the EQ of the days in the range, and the daily
    EQ is checked against its months on the way.

Depends on:
    ``energy.ports``, ``energy.domain``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from energy.domain.energy_cross import AreaCross, EnergyCrossSeries, cross_rows
from energy.domain.equs_series import FREQUENCIES, totals
from energy.domain.exceptions import InvalidPeriodError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from energy.domain.equs_gold import DateRange
    from energy.domain.equs_series import Frequency
    from energy.ports.energy_cross import AreaEnergySource, EquivalentsReader


class EnergyCross:
    """The energy of each plant area against its equivalent batteries.

    Args:
        sources: where the energy of each area comes from, in priority order;
            several sources may answer for one area.
        equivalents: where the EQ comes from.
    """

    def __init__(self, sources: Sequence[AreaEnergySource], equivalents: EquivalentsReader) -> None:
        by_area: dict[str, list[AreaEnergySource]] = {}
        for source in sources:
            for area in source.areas:
                by_area.setdefault(area, []).append(source)
        self._sources = by_area
        self._equivalents = equivalents

    @property
    def areas(self) -> tuple[str, ...]:
        """The areas some source has energy for, in the order of the sources."""
        return tuple(self._sources)

    def cross(
        self,
        period: DateRange,
        *,
        frequency: Frequency = "day",
        areas: Sequence[str] = (),
    ) -> EnergyCrossSeries:
        """Set the energy of each area against its EQ over a range.

        Args:
            period: the days, both ends included.
            frequency: ``day`` or ``month``; a month cut by the range covers
                only the days in it.
            areas: the areas to cross; every area with energy when empty. Case
                does not matter.

        Returns:
            One cross per area: its rows with their energy source, its total
            over the range, its frozen counters and the check of its daily EQ;
            and, for a daily cross of every area, the areas left out because
            their energy is monthly.

        Raises:
            InvalidPeriodError: if the frequency is unknown, an area has no
                energy source, an area named in a daily cross has only monthly
                energy, or the EQ cannot be answered for the range.
            SourceUnavailableError: if an energy source cannot be read.
            RepositoryError: if the EQ store fails.
        """
        if frequency not in FREQUENCIES:
            raise InvalidPeriodError("the frequency is day or month", frequency=frequency)
        crosses = []
        monthly_only = []
        for area in self._chosen(areas):
            energies = [source.energy(area, period) for source in self._sources[area]]
            if frequency == "day":
                energies = [energy for energy in energies if energy.resolution == "day"]
                if not energies:
                    if areas:
                        raise InvalidPeriodError(
                            "this area has only monthly energy; ask for the monthly cross",
                            area=area,
                        )
                    monthly_only.append(area)
                    continue
            series = self._equivalents.series(period, frequency="day", areas=(area,))
            rows, total, eq_without_energy = cross_rows(
                energies,
                {item.start: item for item in totals(series)},
                series.procesos,
                frequency,
            )
            crosses.append(
                AreaCross(
                    sources=tuple(energies),
                    procesos=series.procesos,
                    rows=rows,
                    total=total,
                    eq_without_energy=eq_without_energy,
                    reconciled_months=series.reconciled_months,
                    eq_mismatches=len(series.mismatches),
                )
            )
        return EnergyCrossSeries(
            period=period,
            frequency=frequency,
            areas=tuple(crosses),
            monthly_only=tuple(monthly_only),
        )

    def _chosen(self, areas: Sequence[str]) -> tuple[str, ...]:
        """Return the areas asked for as the sources spell them, in the order asked."""
        if not areas:
            return self.areas
        known = {name.upper(): name for name in self._sources}
        unknown = [name for name in areas if name.upper() not in known]
        if unknown:
            raise InvalidPeriodError(
                "no energy source for this area", areas=unknown, areas_with_energy=list(self.areas)
            )
        return tuple(dict.fromkeys(known[name.upper()] for name in areas))


__all__ = ["EnergyCross"]
