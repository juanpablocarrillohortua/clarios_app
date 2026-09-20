"""Core business entities: Plant, Meter, Reading, Shift.

Layer: domain

Responsibility:
    Defines the things the business talks about and the invariants that hold for
    them regardless of where they are stored. An entity has identity (two Meters
    with the same id are the same meter even if their attributes differ), which
    is what distinguishes these from the value objects in
    :mod:`energy.domain.value_objects`. This module must never import a database
    driver, a dataframe library, or anything that performs I/O; it is the
    vocabulary that Bronze rows are parsed *into* and that Silver and Gold rows
    are computed *from*.

Depends on:
    ``energy.domain.exceptions``, ``energy.domain.value_objects``, and the
    standard library only.

Implemented in:
    Phase 2 - Domain model and KPI engine
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from datetime import datetime, time
    from decimal import Decimal
    from uuid import UUID

    from energy.domain.value_objects import EnergyAmount, TimeWindow


@dataclass(frozen=True, slots=True)
class Plant:
    """An industrial site whose energy and gas consumption is monitored.

    A plant is the top-level aggregation unit: KPIs, targets and baselines are
    all defined per plant. Its ``timezone`` is used only to present results and
    to align shift boundaries; all stored timestamps remain UTC.

    Attributes:
        id: stable identifier, matching ``reference.plants.plant_id``.
        code: short human-facing code used in reports, for example ``"MTY-01"``.
        name: full plant name.
        timezone: IANA timezone name, for example ``"America/Bogota"``. Used for
            shift alignment and report rendering, never for storage.
        commissioned_on: date the plant entered service; readings before it are
            rejected as implausible.
        is_active: whether the plant is currently in scope for ingestion.
    """

    id: UUID
    code: str
    name: str
    timezone: str
    commissioned_on: datetime
    is_active: bool = True

    def __post_init__(self) -> None:
        """Validate the plant invariants.

        Raises:
            DomainRuleError: if ``code`` is empty or ``timezone`` is not a
                recognised IANA name.
        """
        raise NotImplementedError("Phase 2: plant invariant validation")


@dataclass(frozen=True, slots=True)
class Meter:
    """A physical or virtual measuring point belonging to a plant.

    A virtual meter is one whose readings are derived (a sum of submeters, or a
    difference between a main and a branch), which is why ``is_virtual`` exists:
    virtual meters must never be double-counted in a plant total.

    Attributes:
        id: stable identifier, matching ``reference.meters.meter_id``.
        plant_id: the plant this meter belongs to.
        tag: source-system tag, unique within a plant, for example ``"E-MAIN-01"``.
        commodity: what is measured - ``"electricity"``, ``"gas"``, or ``"steam"``.
        unit: canonical unit of the readings, for example ``"kWh"`` or ``"Nm3"``.
        multiplier: factor applied to the raw register value to obtain ``unit``.
        is_virtual: whether readings are derived rather than measured.
        parent_meter_id: the meter this one submeters, if any.
    """

    id: UUID
    plant_id: UUID
    tag: str
    commodity: str
    unit: str
    multiplier: Decimal
    is_virtual: bool = False
    parent_meter_id: UUID | None = None

    def scale(self, raw_value: Decimal) -> EnergyAmount:
        """Convert a raw register value into a canonical energy amount.

        Args:
            raw_value: the value exactly as the source system reported it, before
                any multiplier is applied.

        Returns:
            An :class:`~energy.domain.value_objects.EnergyAmount` in this meter's
            canonical ``unit``.

        Raises:
            UnitMismatchError: if the meter's unit is not a recognised commodity
                unit.
        """
        raise NotImplementedError("Phase 2: raw register scaling")


@dataclass(frozen=True, slots=True)
class Reading:
    """One measurement from one meter over one interval of the canonical grid.

    A Reading is the atomic unit of the Silver layer. ``window`` is an interval,
    not an instant: a reading describes consumption *between* two timestamps,
    and the stored timestamp is always the interval start.

    Attributes:
        meter_id: the meter that produced the measurement.
        window: the canonical-grid interval the measurement covers.
        amount: the measured quantity, in the meter's canonical unit.
        quality: one of :data:`energy.core.constants.QUALITY_FLAGS`.
        source_system: which source this reading was consolidated from, kept so a
            Silver row can still be traced back to its Bronze origin.
    """

    meter_id: UUID
    window: TimeWindow
    amount: EnergyAmount
    quality: str
    source_system: str

    @property
    def is_reliable(self) -> bool:
        """Return whether this reading may be used in KPI computation.

        Returns:
            ``True`` for ``ok``, ``interpolated`` and ``estimated`` readings;
            ``False`` for ``suspect`` and ``missing`` ones, which are carried
            through to Silver for auditability but excluded from Gold.
        """
        raise NotImplementedError("Phase 2: reliability rules per quality flag")

    def merge(self, other: Self) -> Self:
        """Combine two readings for the same meter and window.

        Used by consolidation when several sources report the same interval. The
        source priority defined in ``config/sources.yaml`` decides which value
        wins; the loser is retained in Bronze and recorded in the merge audit.

        Args:
            other: the competing reading. Must share ``meter_id`` and ``window``.

        Returns:
            The winning reading, with ``quality`` downgraded when the two sources
            disagree by more than the configured tolerance.

        Raises:
            DomainRuleError: if ``meter_id`` or ``window`` differ.
        """
        raise NotImplementedError("Phase 3: multi-source reading merge")


@dataclass(frozen=True, slots=True)
class Shift:
    """A named working period used to slice KPIs by operating pattern.

    Shifts are defined in plant-local time (a "night shift" means the same wall
    clock hours all year) and projected onto the UTC grid at query time, which is
    why ``starts_at`` and ``ends_at`` are naive times rather than datetimes.

    Attributes:
        id: stable identifier, matching ``reference.shifts.shift_id``.
        plant_id: the plant this shift pattern belongs to.
        name: human-facing name, for example ``"Night"``.
        starts_at: local start time, inclusive.
        ends_at: local end time, exclusive. May be earlier than ``starts_at``,
            which means the shift crosses midnight.
        weekdays: ISO weekday numbers (1 = Monday) this shift is worked on.
    """

    id: UUID
    plant_id: UUID
    name: str
    starts_at: time
    ends_at: time
    weekdays: frozenset[int]

    @property
    def crosses_midnight(self) -> bool:
        """Return whether this shift spans a calendar day boundary."""
        raise NotImplementedError("Phase 2: midnight-crossing detection")

    def contains(self, moment: datetime, plant_timezone: str) -> bool:
        """Return whether a UTC instant falls inside this shift.

        Args:
            moment: a timezone-aware UTC datetime.
            plant_timezone: the IANA timezone to convert into before comparing.

        Returns:
            ``True`` when the localised instant falls within the shift window on
            a worked weekday.

        Raises:
            NaiveDatetimeError: if ``moment`` is timezone-naive.
        """
        raise NotImplementedError("Phase 2: shift membership test")


__all__ = ["Meter", "Plant", "Reading", "Shift"]
