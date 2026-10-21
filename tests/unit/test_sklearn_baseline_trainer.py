"""The scikit-learn trainer: the notebook's pipeline and validation, on synthetic data.

Skipped where scikit-learn and statsmodels are not installed (the ``analytics``
extra; CI installs no extras). Every expectation is computed here a second
way, with numpy's least squares, so the test does not restate the trainer.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pytest

pytest.importorskip("sklearn")
pytest.importorskip("statsmodels")

from energy.adapters.sklearn_baseline_trainer import SklearnBaselineTrainer
from energy.domain.baseline_models import SPECS, ModelKey, forecast
from energy.domain.exceptions import DataQualityError
from energy.ports.baseline_models import TrainingRow

pytestmark = pytest.mark.unit

MONTHS = 60


def _month(index: int) -> date:
    year, month = divmod(2021 * 12 + index, 12)
    return date(year, month + 1, 1)


def _monthly_rows() -> list[TrainingRow]:
    """kwh = 100,000 + 5 * eq + 6,000 * (dias - 30) + a wobble, over 60 months."""
    rows = []
    for index in range(MONTHS):
        month = _month(index)
        days = float((_month(index + 1) - month).days)
        eq = 50_000.0 + 10_000.0 * math.sin(index / 3) + 300.0 * index
        wobble = 4_000.0 * math.sin(index * 1.7)
        rows.append(
            TrainingRow(
                period=month,
                target=100_000.0 + 5 * eq + 6_000.0 * (days - 30) + wobble,
                features={"eq": eq, "dias": days},
            )
        )
    return rows


def _least_squares(rows: list[TrainingRow]) -> np.ndarray:
    design = np.array([[1.0, row.features["eq"], row.features["dias"]] for row in rows])
    target = np.array([row.target for row in rows])
    coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
    return coefficients


def _predict(coefficients: np.ndarray, row: TrainingRow) -> float:
    return float(coefficients @ [1.0, row.features["eq"], row.features["dias"]])


def test_the_final_model_is_the_least_squares_fit_of_the_window() -> None:
    """OREC's spec: the last 36 months, the days measured from 30 in the equation."""
    rows = _monthly_rows()
    model = SklearnBaselineTrainer().fit(SPECS[ModelKey.OREC_MENSUAL], rows)

    window = rows[-36:]
    intercept, slope_eq, slope_days = _least_squares(window)
    assert model.training_start == window[0].period
    assert model.training_end == window[-1].period
    assert model.n_training == 36
    assert model.coverage == 1.0
    assert model.coefficient("eq").value == pytest.approx(slope_eq)
    assert model.coefficient("dias").value == pytest.approx(slope_days)
    assert model.intercept_at_reference == pytest.approx(intercept + slope_days * 30)
    assert model.coefficient("dias").centre == pytest.approx(
        np.mean([row.features["dias"] for row in window])
    )
    sample = window[-1]
    expected = _predict(_least_squares(window), sample)
    inputs = {"eq": sample.features["eq"], "dias": sample.features["dias"]}
    assert forecast(model, inputs).prediction == pytest.approx(expected)


def test_validation_is_one_step_ahead_on_a_rolling_window() -> None:
    """Each of the last 24 months is forecast from the 36 before it, never from itself."""
    rows = _monthly_rows()
    model = SklearnBaselineTrainer().fit(SPECS[ModelKey.OREC_MENSUAL], rows)

    expected, naive = [], []
    for position in range(MONTHS - 24, MONTHS):
        train = rows[max(0, position - 36) : position]
        expected.append(_predict(_least_squares(train), rows[position]))
        naive.append(rows[position - 1].target)
    validated = [point for point in model.history if point.predicted is not None]

    assert [point.period for point in validated] == [row.period for row in rows[-24:]]
    assert [point.predicted for point in validated] == pytest.approx(expected)
    assert [point.naive for point in validated] == pytest.approx(naive)
    assert len(model.history) == MONTHS

    actual = np.array([row.target for row in rows[-24:]])
    errors = actual - np.array(expected)
    naive_errors = actual - np.array(naive)
    metrics = model.metrics
    assert metrics.validation_periods == 24
    assert metrics.validation_mae == pytest.approx(np.abs(errors).mean())
    assert metrics.validation_wape == pytest.approx(100 * np.abs(errors).sum() / actual.sum())
    assert metrics.validation_mase == pytest.approx(
        np.abs(errors).mean() / np.abs(naive_errors).mean()
    )
    assert metrics.alert_band == pytest.approx(1.6448536269514722 * errors.std(ddof=1))
    assert 0 <= metrics.diebold_mariano_p <= 1
    assert 0 < metrics.train_r2 <= 1


def test_every_coefficient_carries_a_hac_p_value() -> None:
    model = SklearnBaselineTrainer().fit(SPECS[ModelKey.FA_MENSUAL], _monthly_rows())

    assert model.coefficient("eq").p_value < 0.001
    assert model.coefficient("eq").significant
    assert all(0 <= item.p_value <= 1 for item in (model.intercept, *model.coefficients))


def test_the_daily_model_learns_sundays_and_days_without_production() -> None:
    first = date(2026, 7, 1)
    rows = []
    for offset in range(70):
        day = first + timedelta(days=offset)
        sunday = 1.0 if day.weekday() == 6 else 0.0
        eq = 0.0 if offset % 11 == 0 else 2_000.0 + 50.0 * (offset % 7) + 10.0 * offset
        idle = 1.0 if eq == 0 else 0.0
        target = 10_000.0 + 15 * eq - 8_000 * sunday - 1_500 * idle + 300 * math.sin(offset)
        rows.append(TrainingRow(day, target, {"eq": eq, "domingo": sunday, "sin_eq": idle}))

    model = SklearnBaselineTrainer().fit(SPECS[ModelKey.FA_DIARIO], rows)

    assert model.coefficient("eq").value == pytest.approx(15, rel=0.05)
    assert model.coefficient("domingo").value == pytest.approx(-8_000, rel=0.1)
    assert model.metrics.validation_periods == 30
    assert model.intercept_at_reference == pytest.approx(model.intercept.value)


def test_too_few_periods_are_refused() -> None:
    with pytest.raises(DataQualityError, match="Too few periods"):
        SklearnBaselineTrainer().fit(SPECS[ModelKey.OREC_MENSUAL], _monthly_rows()[:20])
