"""Fits the energy baseline models with scikit-learn, and judges them out of sample.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.baseline_models.BaselineTrainer` exactly as
    section 7 of ``notebooks/further_search.ipynb`` does:

    - **The model** is a ``Pipeline``: a ``ColumnTransformer`` that passes each
      variable through, or centres it on its training mean when its spec
      measures it from a reference (``dias``), then a ``LinearRegression``.
    - **Validation** is ``TimeSeriesSplit(n_splits=<validation periods>,
      test_size=1, max_train_size=<window>)``: a fresh pipeline per fold,
      trained on the past only, forecasts the next period. The naive forecast
      of the same period is the last value of its training window. Training
      figures are each fold's fit on its own window, averaged.
    - **The final model** is the same pipeline fitted once on the latest
      window. Its p-values come from ``statsmodels`` OLS with Newey-West (HAC)
      errors on the pipeline's own columns; its coefficients must equal the
      pipeline's.
    - **Diebold-Mariano** with the Harvey correction, for one step ahead, is
      the one-sample t-test of the squared-error differences, which is how it
      is computed here.

    This is the only module that imports scikit-learn or statsmodels (the
    ``analytics`` extra). The flow imports it when it trains; the API never.

Depends on:
    ``energy.domain``, ``energy.ports``, ``numpy``, ``pandas``,
    ``scikit-learn``, ``statsmodels``.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Final, cast

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from statsmodels.stats.weightstats import DescrStatsW

from energy.domain.baseline_models import (
    INTERCEPT,
    BaselineModel,
    Coefficient,
    FeatureRange,
    HistoryPoint,
    ModelMetrics,
    periods_between,
)
from energy.domain.exceptions import DataQualityError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from energy.domain.baseline_models import ModelSpec
    from energy.ports.baseline_models import TrainingRow

#: z of a two-sided 90 % band.
Z90: Final = 1.6448536269514722
#: Training rows a fold needs beyond its variables.
MIN_EXTRA_ROWS: Final = 3


def build_pipeline(spec: ModelSpec) -> Pipeline:
    """The model of one spec: its variables, in order, then a linear regression."""
    steps = [
        (
            f"{feature.name}_centrado" if feature.reference is not None else feature.name,
            StandardScaler(with_std=False) if feature.reference is not None else "passthrough",
            [feature.name],
        )
        for feature in spec.features
    ]
    variables = ColumnTransformer(steps, verbose_feature_names_out=False)
    return Pipeline([("variables", variables), ("regresion", LinearRegression())])


def _frame(rows: Sequence[TrainingRow], spec: ModelSpec) -> pd.DataFrame:
    """The rows as a frame indexed by period, the target last."""
    frame = pd.DataFrame(
        [{**{name: row.features[name] for name in spec.feature_names}} for row in rows],
        index=pd.Index([row.period for row in rows], name="period"),
    )
    frame[spec.target] = [row.target for row in rows]
    return frame.sort_index()


def _wape(actual: pd.Series, predicted: Any) -> float:
    """Σ|error| / Σ|actual|, in %."""
    return float(100 * np.abs(actual - predicted).sum() / np.abs(actual).sum())


def _p(value: Any) -> float:
    """A p-value, or 1.0 where it is undefined (a variable that never varied)."""
    number = float(value)
    return number if math.isfinite(number) else 1.0


def _diebold_mariano_p(model_errors: pd.Series, naive_errors: pd.Series) -> float:
    """Two-sided p-value of the model against the naive forecast, squared loss."""
    difference = (model_errors**2 - naive_errors**2).to_numpy()
    if np.allclose(difference, difference[0]):
        return 1.0
    _, p_value, _ = DescrStatsW(difference).ttest_mean(0.0)
    return _p(p_value)


class SklearnBaselineTrainer:
    """Trains a baseline model as the notebook does, and validates it one step ahead."""

    def fit(self, spec: ModelSpec, rows: Sequence[TrainingRow]) -> BaselineModel:
        """Train on the latest window and validate with ``TimeSeriesSplit``.

        Raises:
            DataQualityError: if there are too few rows for the validation.
        """
        frame = _frame(rows, spec)
        features = list(spec.feature_names)
        needed = spec.validation_periods + len(features) + MIN_EXTRA_ROWS
        if len(frame) < needed:
            raise DataQualityError(
                "Too few periods to train and validate the model",
                model_key=spec.key.value,
                periods=len(frame),
                needed=needed,
            )

        validation, train_r2, train_wape = self._backtest(spec, frame, features)
        actual = frame[spec.target].loc[validation.index]
        errors = actual - validation["predicted"]
        naive_errors = actual - validation["naive"]
        naive_mae = float(naive_errors.abs().mean())
        if naive_mae == 0:
            raise DataQualityError(
                "The naive forecast is exact over the validation: nothing to compare",
                model_key=spec.key.value,
            )
        mae = float(errors.abs().mean())
        metrics = ModelMetrics(
            train_r2=train_r2,
            train_wape=train_wape,
            validation_r2=float(r2_score(actual, validation["predicted"])),
            validation_mae=mae,
            validation_wape=_wape(actual, validation["predicted"]),
            validation_mase=mae / naive_mae,
            naive_wape=_wape(actual, validation["naive"]),
            diebold_mariano_p=_diebold_mariano_p(errors, naive_errors),
            alert_band=float(Z90 * errors.std(ddof=1)),
            validation_periods=len(validation),
        )

        window = frame if spec.training_window is None else frame.iloc[-spec.training_window :]
        intercept, coefficients = self._final(spec, window, features)
        periods = cast("list[date]", frame.index.tolist())
        tested = cast("list[date]", validation.index.tolist())
        predicted = dict(zip(tested, validation["predicted"].tolist(), strict=True))
        naive = dict(zip(tested, validation["naive"].tolist(), strict=True))
        records = frame.to_dict("records")
        history = tuple(
            HistoryPoint(
                period=period,
                actual=float(record[spec.target]),
                inputs={name: float(record[name]) for name in features},
                predicted=None if period not in predicted else float(predicted[period]),
                naive=None if period not in naive else float(naive[period]),
            )
            for period, record in zip(periods, records, strict=True)
        )
        start, end = window.index.min(), window.index.max()
        return BaselineModel(
            spec=spec,
            intercept=intercept,
            coefficients=coefficients,
            metrics=metrics,
            history=history,
            ranges=tuple(
                FeatureRange(name, float(window[name].min()), float(window[name].max()))
                for name in features
            ),
            training_start=start,
            training_end=end,
            n_training=len(window),
            coverage=len(window) / periods_between(start, end, spec.granularity),
        )

    @staticmethod
    def _backtest(
        spec: ModelSpec, frame: pd.DataFrame, features: list[str]
    ) -> tuple[pd.DataFrame, float, float]:
        """One-step forecasts of a fresh pipeline per fold, and the folds' training fit."""
        splitter = TimeSeriesSplit(
            n_splits=spec.validation_periods, test_size=1, max_train_size=spec.training_window
        )
        rows, r2s, wapes = [], [], []
        for train_index, test_index in splitter.split(frame):
            train, test = frame.iloc[train_index], frame.iloc[test_index]
            model = build_pipeline(spec).fit(train[features], train[spec.target])
            r2s.append(float(model.score(train[features], train[spec.target])))
            wapes.append(_wape(train[spec.target], model.predict(train[features])))
            rows.append(
                {
                    "period": test.index[0],
                    "predicted": float(model.predict(test[features])[0]),
                    "naive": float(train[spec.target].iloc[-1]),
                }
            )
        validation = pd.DataFrame(rows).set_index("period")
        return validation, float(np.mean(r2s)), float(np.mean(wapes))

    @staticmethod
    def _final(
        spec: ModelSpec, window: pd.DataFrame, features: list[str]
    ) -> tuple[Coefficient, tuple[Coefficient, ...]]:
        """The final pipeline's coefficients, with their HAC p-values."""
        model = build_pipeline(spec).fit(window[features], window[spec.target])
        variables = model.named_steps["variables"]
        regression = model.named_steps["regresion"]
        design = pd.DataFrame(
            variables.transform(window[features]),
            index=window.index,
            columns=variables.get_feature_names_out(),
        )
        robust = sm.OLS(
            window[spec.target].astype(float), sm.add_constant(design, has_constant="add")
        ).fit(cov_type="HAC", cov_kwds={"maxlags": spec.hac_lags})
        expected = np.array([regression.intercept_, *regression.coef_])
        if not np.allclose(robust.params.to_numpy(), expected, rtol=1e-6, atol=1e-6):
            raise DataQualityError(  # pragma: no cover - a bug, not a data problem
                "statsmodels and scikit-learn disagree on the coefficients",
                model_key=spec.key.value,
            )
        p_values = robust.pvalues
        centres = {
            feature.name: float(variables.named_transformers_[f"{feature.name}_centrado"].mean_[0])
            for feature in spec.features
            if feature.reference is not None
        }
        intercept = Coefficient(INTERCEPT, float(regression.intercept_), _p(p_values["const"]))
        coefficients = tuple(
            Coefficient(
                feature=name,
                value=float(value),
                p_value=_p(p_values[name]),
                centre=centres.get(name),
            )
            for name, value in zip(features, regression.coef_, strict=True)
        )
        return intercept, coefficients


__all__ = ["SklearnBaselineTrainer", "build_pipeline"]
