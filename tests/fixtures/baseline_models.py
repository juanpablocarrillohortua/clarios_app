"""Stand-ins for the energy baseline models: a hand-made model, and a store in memory.

:func:`orec_model` and :func:`daily_model` build models whose numbers are
round and known, so a test can work out every forecast by hand. The store
keeps the promises of :class:`~energy.ports.baseline_models.BaselineModelStore`
that ``tests/integration/test_baseline_models_store.py`` checks the PostgreSQL
adapter against: storing supersedes, and only the current model is served.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from energy.domain.baseline_models import (
    INTERCEPT,
    SPECS,
    BaselineModel,
    Coefficient,
    FeatureRange,
    HistoryPoint,
    ModelKey,
    ModelMetrics,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

TRAINED_AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

METRICS = ModelMetrics(
    train_r2=0.75,
    train_wape=3.82,
    validation_r2=0.91,
    validation_mae=27_085.0,
    validation_wape=4.89,
    validation_mase=0.48,
    naive_wape=10.21,
    diebold_mariano_p=0.027,
    alert_band=50_000.0,
    validation_periods=2,
)


def _months(first: date, count: int) -> list[date]:
    months = [first]
    for _ in range(count - 1):
        last = months[-1]
        months.append(
            date(last.year + 1, 1, 1) if last.month == 12 else date(last.year, last.month + 1, 1)
        )
    return months


def orec_model() -> BaselineModel:
    """OREC's monthly model: kwh = 100,000 + 5 * eq + 6,000 * (dias - 30).

    It was fitted with ``dias`` centred on 30.5, so its stored intercept is
    100,000 + 6,000 * 0.5 = 103,000. Its history is three months, the last
    two of them validated; the last month is 2026-06, so the next is 2026-07.
    """
    months = _months(date(2026, 4, 1), 3)
    eqs = [60_000.0, 70_000.0, 80_000.0]
    history = tuple(
        HistoryPoint(
            period=month,
            actual=100_000.0 + 5 * eq,
            inputs={"eq": eq, "dias": 30.0},
            predicted=None if index == 0 else 100_000.0 + 5 * eq + 1_000.0,
            naive=None if index == 0 else 100_000.0 + 5 * eqs[index - 1],
        )
        for index, (month, eq) in enumerate(zip(months, eqs, strict=True))
    )
    return BaselineModel(
        spec=SPECS[ModelKey.OREC_MENSUAL],
        intercept=Coefficient(INTERCEPT, 103_000.0, 0.0001),
        coefficients=(
            Coefficient("eq", 5.0, 0.00001),
            Coefficient("dias", 6_000.0, 0.108, centre=30.5),
        ),
        metrics=METRICS,
        history=history,
        ranges=(FeatureRange("eq", 40_000.0, 90_000.0), FeatureRange("dias", 28.0, 31.0)),
        training_start=months[0],
        training_end=months[-1],
        n_training=3,
        coverage=1.0,
        trained_at=TRAINED_AT,
    )


def daily_model() -> BaselineModel:
    """F&A's daily model: kwh = 10,000 + 15 * eq - 8,000 * domingo - 1,500 * sin_eq.

    Its history runs from Thursday 2026-09-24 to Wednesday 2026-09-30, so the
    next day is a Thursday. Sunday 2026-09-27 produced nothing.
    """
    first = date(2026, 9, 24)
    days = [first + timedelta(days=offset) for offset in range(7)]
    eqs = [2_000.0, 2_200.0, 1_800.0, 0.0, 2_400.0, 2_100.0, 1_900.0]
    history = tuple(
        HistoryPoint(
            period=day,
            actual=10_000.0 + 15 * eq,
            inputs={
                "eq": eq,
                "domingo": 1.0 if day.weekday() == 6 else 0.0,
                "sin_eq": 1.0 if eq == 0 else 0.0,
            },
        )
        for day, eq in zip(days, eqs, strict=True)
    )
    return BaselineModel(
        spec=SPECS[ModelKey.FA_DIARIO],
        intercept=Coefficient(INTERCEPT, 10_000.0, 0.0001),
        coefficients=(
            Coefficient("eq", 15.0, 0.00001),
            Coefficient("domingo", -8_000.0, 0.012),
            Coefficient("sin_eq", -1_500.0, 0.544),
        ),
        metrics=replace(METRICS, alert_band=9_000.0, validation_periods=30),
        history=history,
        ranges=(
            FeatureRange("eq", 0.0, 3_000.0),
            FeatureRange("domingo", 0.0, 1.0),
            FeatureRange("sin_eq", 0.0, 1.0),
        ),
        training_start=days[0],
        training_end=days[-1],
        n_training=7,
        coverage=1.0,
        trained_at=TRAINED_AT,
    )


class InMemoryBaselineModelStore:
    """Every model ever stored, the latest of each key current."""

    def __init__(self, *models: BaselineModel) -> None:
        self.rows: list[tuple[BaselineModel, bool]] = [(model, True) for model in models]

    def replace(self, models: Sequence[BaselineModel]) -> list[BaselineModel]:
        keys = {model.spec.key for model in models}
        self.rows = [
            (model, current and model.spec.key not in keys) for model, current in self.rows
        ]
        stored = [replace(model, trained_at=TRAINED_AT) for model in models]
        self.rows += [(model, True) for model in stored]
        return stored

    def current(self, key: ModelKey) -> BaselineModel | None:
        return next(
            (model for model, current in self.rows if current and model.spec.key is key), None
        )

    def list_current(self) -> list[BaselineModel]:
        return [model for model, current in self.rows if current]
