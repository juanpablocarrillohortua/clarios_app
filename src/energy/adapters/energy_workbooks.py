"""The plant's energy workbooks, read into the rows a baseline model is trained on.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.baseline_models.BaselineTrainingSource`.
    The energy figures were never loaded into the database: they are in two
    workbooks of ``data/`` (``ENERGY_DATA_DIR``), read here as section 7 of
    ``notebooks/further_search.ipynb`` reads them.

    - **Monthly** (``Base_de_datos.xlsx``): the total row of the area's sheet,
      column ``KWH``, and its ``Produccion (Equivalente)``. Months are read in
      the file's order: a label that breaks the sequence by exactly one year
      (a December typed with the next year) is put back where it belongs. A
      zero is an empty cell: missing, never zero.
    - **Daily** (``SCADA-Base de datos F&A.xlsx``): the hourly counters of the
      three rectifiers, interpolated at each local midnight; a day's energy is
      the difference between two midnights, in kWh. A counter that stands
      still while the others run is frozen, and its day is missing. A day
      needs all three rectifiers. The day's production comes from Gold
      (FORMACION + DECORADO), through ``daily_production``.

    The SCADA stamps are the plant's local time, and a day is a local calendar
    day; nothing here is stored as a timestamp.

    Unlike the notebook, a missing day is not interpolated: an invented day
    would be trained on as if it had been measured.

Depends on:
    ``energy.domain``, ``energy.ports``, ``pandas`` (with the ``calamine`` engine).
"""

from __future__ import annotations

import calendar
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

import pandas as pd

from energy.domain.baseline_models import SUNDAY, Granularity
from energy.domain.exceptions import SourceUnavailableError
from energy.ports.baseline_models import TrainingRow

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from energy.domain.baseline_models import ModelSpec

ENERGY_WORKBOOK: Final = "Base_de_datos.xlsx"
SCADA_FA_WORKBOOK: Final = "SCADA-Base de datos F&A.xlsx"
SCADA_SHEET: Final = "Dataset 1"
RECTIFIERS: Final = ("RECTIFICADOR 1", "RECTIFICADOR 2", "RECTIFICADOR 3")
#: The counters are in Wh.
WH_PER_KWH: Final = 1000
#: A day whose other rectifiers used more than this many kWh while one stood still.
FROZEN_WHEN_OTHERS_ABOVE_KWH: Final = 1000
MONTHS_IN_YEAR: Final = 12

MONTHS: Final[Mapping[str, int]] = {
    "Ene": 1, "Jan": 1, "Feb": 2, "Mar": 3, "Abr": 4, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Ago": 8, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dic": 12, "Dec": 12,
}  # fmt: skip


def _next_month(month: date) -> date:
    """The first day of the month after ``month``."""
    if month.month == MONTHS_IN_YEAR:
        return date(month.year + 1, 1, 1)
    return date(month.year, month.month + 1, 1)


def _repaired(labels: Sequence[date]) -> list[date]:
    """Put back a month whose label is one year off the file's sequence."""
    months: list[date] = []
    for label in labels:
        month = label
        if months:
            expected = _next_month(months[-1])
            if (
                month != expected
                and month.month == expected.month
                and abs(month.year - expected.year) == 1
            ):
                month = expected
        months.append(month)
    return months


def _positive(value: object) -> float | None:
    """A cell as a positive number, or ``None``: an empty cell, text and zero are missing."""
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number) or number <= 0:
        return None
    return float(number)


def read_monthly_totals(path: Path, sheet: str) -> list[tuple[date, float | None, float | None]]:
    """Read the total rows of one area's sheet: month, kWh and EQ.

    Args:
        path: the energy workbook.
        sheet: the area's sheet; its total row has the sheet's name in ``Proceso``.

    Returns:
        One ``(month, kwh, eq)`` per total row, in the file's order, with a
        missing value as ``None``.

    Raises:
        SourceUnavailableError: if the workbook or the sheet cannot be read.
    """
    try:
        raw = pd.read_excel(path, sheet_name=sheet, header=1, engine="calamine")
    except (OSError, ValueError) as exc:
        raise SourceUnavailableError(
            "The energy workbook cannot be read", path=str(path), sheet=sheet
        ) from exc
    raw.columns = [str(column).strip() for column in raw.columns]
    year_column = raw.columns[1]
    total = raw["Proceso"].astype(str).str.strip() == sheet
    rows = raw[total & raw["Mes"].notna()]
    labels = [
        date(int(year), MONTHS[str(month).strip()], 1)
        for year, month in zip(rows[year_column], rows["Mes"], strict=True)
    ]
    months = _repaired(labels)
    return [
        (month, _positive(kwh), _positive(eq))
        for month, kwh, eq in zip(
            months, rows["KWH"], rows["Produccion (Equivalente)"], strict=True
        )
    ]


def read_rectifier_days(path: Path) -> dict[date, float]:
    """Read the energy of the three F&A rectifiers, day by day, in kWh.

    Args:
        path: the SCADA workbook of F&A.

    Returns:
        The kWh of every day with all three counters running; other days are absent.

    Raises:
        SourceUnavailableError: if the workbook cannot be read.
    """
    try:
        raw = pd.read_excel(path, sheet_name=SCADA_SHEET, engine="calamine")
    except (OSError, ValueError) as exc:
        raise SourceUnavailableError("The SCADA workbook cannot be read", path=str(path)) from exc
    raw["t_stamp"] = pd.to_datetime(raw["t_stamp"])
    counters = raw.sort_values("t_stamp").set_index("t_stamp")[list(RECTIFIERS)].astype(float)
    midnights = pd.date_range(
        counters.index.min().ceil("D"), counters.index.max().floor("D"), freq="D"
    )
    at_midnight = (
        counters.reindex(counters.index.union(midnights)).interpolate(method="time").loc[midnights]
    )
    per_day = at_midnight.diff().shift(-1).iloc[:-1] / WH_PER_KWH
    total = per_day.sum(axis=1)
    frozen = per_day.le(0) & total.gt(FROZEN_WHEN_OTHERS_ABOVE_KWH).to_numpy()[:, None]
    days = per_day.mask(frozen).sum(axis=1, min_count=len(RECTIFIERS)).dropna()
    stamps = cast("list[pd.Timestamp]", days.index.tolist())
    return {stamp.date(): float(kwh) for stamp, kwh in zip(stamps, days.tolist(), strict=True)}


class EnergyWorkbookSource:
    """The training rows of the baseline models, from the workbooks and Gold's daily production."""

    def __init__(
        self, data_dir: Path, daily_production: Callable[[], Mapping[date, float]]
    ) -> None:
        """Build the source.

        Args:
            data_dir: the folder holding the energy workbooks.
            daily_production: returns the F&A equivalent batteries of every
                day the database covers (zero on a day without production).
        """
        self._data_dir = Path(data_dir)
        self._daily_production = daily_production

    def rows(self, spec: ModelSpec) -> list[TrainingRow]:
        """Return every complete period of the model, oldest first."""
        if spec.granularity is Granularity.MONTH:
            return self._monthly(spec)
        return self._daily()

    def _monthly(self, spec: ModelSpec) -> list[TrainingRow]:
        """The area's months with both energy and production."""
        totals = read_monthly_totals(self._data_dir / ENERGY_WORKBOOK, spec.area)
        rows = [
            TrainingRow(
                period=month,
                target=kwh,
                features={
                    "eq": eq,
                    "dias": float(calendar.monthrange(month.year, month.month)[1]),
                },
            )
            for month, kwh, eq in totals
            if kwh is not None and eq is not None
        ]
        return sorted(rows, key=lambda row: row.period)

    def _daily(self) -> list[TrainingRow]:
        """The F&A days with rectifier energy, inside the period the database covers."""
        energy = read_rectifier_days(self._data_dir / SCADA_FA_WORKBOOK)
        production = self._daily_production()
        rows = []
        for day in sorted(energy):
            if day not in production:
                continue
            eq = production[day]
            rows.append(
                TrainingRow(
                    period=day,
                    target=energy[day],
                    features={
                        "eq": eq,
                        "domingo": 1.0 if day.weekday() == SUNDAY else 0.0,
                        "sin_eq": 1.0 if eq <= 0 else 0.0,
                    },
                )
            )
        return rows


__all__ = ["EnergyWorkbookSource", "read_monthly_totals", "read_rectifier_days"]
