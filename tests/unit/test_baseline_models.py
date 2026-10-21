"""The energy baseline models: the forecast's arithmetic, its checks, and the service.

The models are the hand-made ones of ``tests/fixtures/baseline_models.py``,
whose forecasts are worked out here by hand. Training is tested in
``test_sklearn_baseline_trainer.py``, the API in ``test_api_models.py``.
"""

from __future__ import annotations

import json
import math
from datetime import date
from typing import TYPE_CHECKING

import pytest
from tests.fixtures.baseline_models import InMemoryBaselineModelStore, daily_model, orec_model

from energy.domain.baseline_models import (
    SPECS,
    Granularity,
    ModelKey,
    default_inputs,
    describe_model,
    equation,
    forecast,
    next_period,
    periods_between,
)
from energy.domain.exceptions import (
    ConfigurationError,
    InvalidForecastInputError,
    ModelNotTrainedError,
    UnknownModelError,
)
from energy.ports.baseline_models import TrainingRow
from energy.services.baseline_model_service import BaselineModelService

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


# =============================================================================
# The specs
# =============================================================================
def test_three_models_and_no_ensamble() -> None:
    """Ensamble has no model: its R² is too low (ADR 0012)."""
    assert list(SPECS) == [ModelKey.OREC_MENSUAL, ModelKey.FA_MENSUAL, ModelKey.FA_DIARIO]
    assert {spec.area for spec in SPECS.values()} == {"OREC", "F&A"}


def test_the_specs_are_those_of_the_notebook() -> None:
    """Section 7 of further_search.ipynb: windows, validation folds and HAC lags."""
    orec, fa, daily = (SPECS[key] for key in ModelKey)
    assert orec.feature_names == fa.feature_names == ("eq", "dias")
    assert daily.feature_names == ("eq", "domingo", "sin_eq")
    assert (orec.training_window, fa.training_window, daily.training_window) == (36, None, None)
    assert (orec.validation_periods, fa.validation_periods, daily.validation_periods) == (
        24,
        24,
        30,
    )
    assert (orec.hac_lags, fa.hac_lags, daily.hac_lags) == (3, 3, 7)
    assert daily.granularity is Granularity.DAY
    assert not daily.feature("sin_eq").entered


def test_every_model_and_variable_has_its_words_in_the_web_app(repo_root: Path) -> None:
    """The app names models and variables by the keys the API sends (models/texts.ts)."""
    texts = json.loads((repo_root / "frontend/src/i18n/es.json").read_text(encoding="utf-8"))
    models = texts["models"]

    for key, spec in SPECS.items():
        assert models["names"][key.value], key
        assert models["predicts"][key.value], key
        for name in ("intercepto", *spec.feature_names):
            entry = models["features"][name]
            assert entry["label"], name
            assert entry["what"], name
            assert "{{value}}" in entry["effect"], name


# =============================================================================
# The equation
# =============================================================================
def test_the_intercept_is_read_with_the_days_measured_from_30() -> None:
    """Fitted around 30.5 days, it is shown for a 30-day month: the predictions do not change."""
    assert orec_model().intercept_at_reference == pytest.approx(100_000.0)


def test_the_equation_as_text() -> None:
    assert equation(orec_model()) == "kwh = 100,000 + 5.00 * eq + 6,000 * (dias - 30)"
    assert equation(daily_model()) == (
        "kwh = 10,000 + 15.00 * eq - 8,000 * domingo - 1,500 * sin_eq"
    )


def test_describe_model_names_its_window_and_both_kinds_of_figure() -> None:
    lines = describe_model(orec_model())

    assert lines[0].startswith("orec_mensual: kwh = 100,000")
    assert "2026-04-01 to 2026-06-01 (3 months" in lines[1]
    assert "training (mean of folds): R2 0.75" in lines[2]
    assert "validation (2 months one step ahead): R2 0.91, WAPE 4.89 %" in lines[3]
    assert "dias p=0.108" in lines[4]


# =============================================================================
# A forecast
# =============================================================================
def test_a_monthly_forecast_adds_up_its_terms() -> None:
    result = forecast(orec_model(), {"eq": 70_000, "dias": 31})

    assert result.period == date(2026, 7, 1)
    assert result.intercept == pytest.approx(100_000.0)
    assert [(term.feature, term.value, term.reference) for term in result.terms] == [
        ("eq", 70_000.0, None),
        ("dias", 31.0, 30.0),
    ]
    assert [term.contribution for term in result.terms] == pytest.approx([350_000.0, 6_000.0])
    assert result.prediction == pytest.approx(456_000.0)
    assert (result.low, result.high) == pytest.approx((406_000.0, 506_000.0))
    assert result.per_unit == pytest.approx(456_000 / 70_000)
    assert result.outside_training == ()


def test_a_value_outside_the_training_range_is_flagged() -> None:
    """The forecast is still given: it is an extrapolation, and the app says so."""
    result = forecast(orec_model(), {"eq": 100_000, "dias": 30})

    assert result.outside_training == ("eq",)
    assert result.prediction == pytest.approx(600_000.0)


def test_a_day_without_production_is_derived_from_its_eq() -> None:
    """``sin_eq`` is never entered: it is 1 exactly when ``eq`` is 0."""
    sunday_off = forecast(daily_model(), {"eq": 0, "domingo": 1})
    weekday = forecast(daily_model(), {"eq": 2_000, "domingo": 0})

    assert [(term.feature, term.value) for term in sunday_off.terms] == [
        ("eq", 0.0),
        ("domingo", 1.0),
        ("sin_eq", 1.0),
    ]
    assert sunday_off.prediction == pytest.approx(10_000 - 8_000 - 1_500)
    # The band would reach below zero: energy is not negative.
    assert sunday_off.low == 0.0
    assert sunday_off.per_unit is None
    assert weekday.prediction == pytest.approx(40_000.0)
    assert weekday.terms[-1].value == 0.0
    assert weekday.period == date(2026, 10, 1)


@pytest.mark.parametrize(
    ("inputs", "message"),
    [
        ({"eq": 1, "dias": 30, "otra": 1}, "Unknown variables"),
        ({"eq": 1}, "Missing variables"),
        ({"eq": 1, "dias": 27}, "Below the minimum"),
        ({"eq": 1, "dias": 32}, "Above the maximum"),
        ({"eq": -1, "dias": 30}, "Below the minimum"),
        ({"eq": math.nan, "dias": 30}, "Not a number"),
        ({"eq": math.inf, "dias": 30}, "Not a number"),
    ],
)
def test_a_monthly_forecast_refuses(inputs: dict[str, float], message: str) -> None:
    with pytest.raises(InvalidForecastInputError, match=message):
        forecast(orec_model(), inputs)


@pytest.mark.parametrize(
    ("inputs", "message"),
    [
        ({"eq": 1, "domingo": 0.5}, "Must be 0 or 1"),
        ({"eq": 1, "domingo": 0, "sin_eq": 0}, "Unknown variables"),
    ],
)
def test_a_daily_forecast_refuses(inputs: dict[str, float], message: str) -> None:
    with pytest.raises(InvalidForecastInputError, match=message):
        forecast(daily_model(), inputs)


# =============================================================================
# Where a forecast starts
# =============================================================================
def test_a_month_starts_from_its_days_and_the_typical_production() -> None:
    """July has 31 days; the median of the last months is 70,000 EQ."""
    assert default_inputs(orec_model()) == {"dias": 31.0, "eq": 70_000.0}


def test_a_day_starts_from_its_weekday_and_alike_days() -> None:
    """2026-10-01 is a Thursday: the median of the weekdays that produced."""
    assert default_inputs(daily_model()) == {"domingo": 0.0, "eq": 2_050.0}


def test_next_period_and_periods_between() -> None:
    assert next_period(date(2025, 12, 1), Granularity.MONTH) == date(2026, 1, 1)
    assert next_period(date(2026, 2, 28), Granularity.DAY) == date(2026, 3, 1)
    assert periods_between(date(2023, 7, 1), date(2026, 6, 1), Granularity.MONTH) == 36
    assert periods_between(date(2026, 7, 2), date(2026, 9, 30), Granularity.DAY) == 91


# =============================================================================
# The service
# =============================================================================
def test_the_service_serves_the_current_models_in_the_specs_order() -> None:
    service = BaselineModelService(InMemoryBaselineModelStore(daily_model(), orec_model()))

    assert [model.spec.key for model in service.list_models()] == [
        ModelKey.OREC_MENSUAL,
        ModelKey.FA_DIARIO,
    ]
    assert service.model("fa_diario").spec.key is ModelKey.FA_DIARIO
    assert service.defaults("orec_mensual") == {"dias": 31.0, "eq": 70_000.0}
    assert service.forecast("orec_mensual", {"eq": 70_000, "dias": 31}).prediction == (
        pytest.approx(456_000.0)
    )


def test_an_unknown_model_and_one_never_trained_are_told_apart() -> None:
    service = BaselineModelService(InMemoryBaselineModelStore(orec_model()))

    with pytest.raises(UnknownModelError):
        service.model("ensamble_mensual")
    with pytest.raises(ModelNotTrainedError):
        service.model("fa_mensual")


class _Source:
    def __init__(self) -> None:
        self.asked: list[ModelKey] = []

    def rows(self, spec: object) -> list[TrainingRow]:
        self.asked.append(spec.key)  # type: ignore[attr-defined]
        return [TrainingRow(date(2026, 1, 1), 1.0, {"eq": 1.0, "dias": 31.0})]


class _Trainer:
    def fit(self, spec: object, _rows: object) -> object:
        model = orec_model() if spec.key is ModelKey.OREC_MENSUAL else daily_model()  # type: ignore[attr-defined]
        from dataclasses import replace  # noqa: PLC0415

        return replace(model, trained_at=None)


def test_training_stores_every_model_and_supersedes_the_old_one() -> None:
    store = InMemoryBaselineModelStore(orec_model())
    source = _Source()
    service = BaselineModelService(store, source=source, trainer=_Trainer())  # type: ignore[arg-type]

    trained = service.train([ModelKey.OREC_MENSUAL, ModelKey.FA_DIARIO])

    assert source.asked == [ModelKey.OREC_MENSUAL, ModelKey.FA_DIARIO]
    assert all(model.trained_at is not None for model in trained)
    assert [current for _, current in store.rows] == [False, True, True]


def test_a_dry_run_stores_nothing() -> None:
    store = InMemoryBaselineModelStore()
    service = BaselineModelService(store, source=_Source(), trainer=_Trainer())  # type: ignore[arg-type]

    trained = service.train(dry_run=True)

    assert len(trained) == 3
    assert store.rows == []


def test_training_needs_a_source_and_a_trainer() -> None:
    with pytest.raises(ConfigurationError):
        BaselineModelService(InMemoryBaselineModelStore()).train()
