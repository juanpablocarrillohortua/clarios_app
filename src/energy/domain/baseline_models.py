"""The energy baseline models: what each one predicts, and how a forecast is computed.

Layer: domain

Responsibility:
    Names the three models the web app shows (ADR 0012), what each predicts
    and from which variables (:data:`SPECS`), holds a trained model as plain
    data (:class:`BaselineModel`), and evaluates its equation for the values a
    person enters (:func:`forecast`). Training is not here: the trainer adapter
    fits the models with scikit-learn, and the API never imports it.

    Every model is a straight line: the target is an intercept plus one
    coefficient per variable. A variable may be measured from a reference
    (``dias`` from 30), so the intercept reads as "the energy of a 30-day month
    with no production". A variable may also follow from another (``sin_eq`` is
    1 exactly when ``eq`` is 0): a person never enters it.

    The models are those of section 7 of ``notebooks/further_search.ipynb``.
    Ensamble has none: its R² is too low for a forecast worth showing.

Never:
    Read files or the database, or import a statistics library.

Depends on:
    The standard library and ``energy.domain.exceptions``.
"""

from __future__ import annotations

import calendar
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from energy.domain.exceptions import InvalidForecastInputError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

#: The significance level of the coefficients' p-values.
ALPHA: Final = 0.05
#: How many earlier periods the default production is the median of.
DEFAULT_LOOKBACK: Final = 12
#: The name of the intercept, wherever a coefficient is named.
INTERCEPT: Final = "intercepto"
#: Sunday, as :meth:`datetime.date.weekday` numbers it.
SUNDAY: Final = 6


class ModelKey(StrEnum):
    """The models the web app shows."""

    OREC_MENSUAL = "orec_mensual"
    FA_MENSUAL = "fa_mensual"
    FA_DIARIO = "fa_diario"


class Granularity(StrEnum):
    """The period one forecast covers."""

    MONTH = "month"
    DAY = "day"


class FeatureKind(StrEnum):
    """What values a variable takes."""

    NUMERIC = "numeric"
    BINARY = "binary"


class Derivation(StrEnum):
    """How a variable a person does not enter follows from the ones they do."""

    #: 1 when the period produced no equivalent batteries, else 0.
    NO_PRODUCTION = "no_production"


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """One variable of a model.

    Attributes:
        name: the variable's name, as the trainer and the API call it.
        kind: numeric, or binary (0 or 1).
        minimum: the smallest value a person may enter; ``None`` for none.
        maximum: the largest value a person may enter; ``None`` for none.
        reference: the value the variable is measured from in the equation,
            or ``None`` when it enters as it is.
        derivation: how the variable follows from the others, or ``None``
            when a person enters it.
    """

    name: str
    kind: FeatureKind
    minimum: float | None = None
    maximum: float | None = None
    reference: float | None = None
    derivation: Derivation | None = None

    @property
    def entered(self) -> bool:
        """Whether a person enters this variable (it is not derived)."""
        return self.derivation is None


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """What a model predicts, from what, and how it is trained and judged.

    Attributes:
        key: the model's key.
        area: the plant area whose energy it predicts.
        granularity: a month or a day.
        target: the name of the predicted quantity.
        unit: the unit of the predicted quantity.
        features: the variables, in the order of the equation.
        training_window: how many of the latest periods it is trained on, or
            ``None`` for all of them.
        validation_periods: how many one-step forecasts judge it.
        hac_lags: the lags of the Newey-West errors behind its p-values.
        sources: where its data come from, in words for a person.
    """

    key: ModelKey
    area: str
    granularity: Granularity
    target: str
    unit: str
    features: tuple[FeatureSpec, ...]
    training_window: int | None
    validation_periods: int
    hac_lags: int
    sources: tuple[str, ...]

    @property
    def feature_names(self) -> tuple[str, ...]:
        """The variables' names, in the order of the equation."""
        return tuple(feature.name for feature in self.features)

    def feature(self, name: str) -> FeatureSpec:
        """Return the variable of that name.

        Raises:
            KeyError: if the model has no such variable.
        """
        for feature in self.features:
            if feature.name == name:
                return feature
        raise KeyError(name)


_EQ = FeatureSpec("eq", FeatureKind.NUMERIC, minimum=0.0)
_DAYS = FeatureSpec("dias", FeatureKind.NUMERIC, minimum=28.0, maximum=31.0, reference=30.0)
_SUNDAY = FeatureSpec("domingo", FeatureKind.BINARY, minimum=0.0, maximum=1.0)
_NO_PRODUCTION = FeatureSpec(
    "sin_eq", FeatureKind.BINARY, minimum=0.0, maximum=1.0, derivation=Derivation.NO_PRODUCTION
)
_WORKBOOK = "data/Base_de_datos.xlsx"

#: The three models, by key.
SPECS: Final[Mapping[ModelKey, ModelSpec]] = MappingProxyType(
    {
        ModelKey.OREC_MENSUAL: ModelSpec(
            key=ModelKey.OREC_MENSUAL,
            area="OREC",
            granularity=Granularity.MONTH,
            target="kwh",
            unit="kWh",
            features=(_EQ, _DAYS),
            # OREC's relation broke in September 2023: only the recent one is used.
            training_window=36,
            validation_periods=24,
            hac_lags=3,
            sources=(f"{_WORKBOOK}, hoja OREC (fila total: KWH y Produccion (Equivalente))",),
        ),
        ModelKey.FA_MENSUAL: ModelSpec(
            key=ModelKey.FA_MENSUAL,
            area="F&A",
            granularity=Granularity.MONTH,
            target="kwh",
            unit="kWh",
            features=(_EQ, _DAYS),
            # F&A's relation has no break: all its history is used.
            training_window=None,
            validation_periods=24,
            hac_lags=3,
            sources=(f"{_WORKBOOK}, hoja F&A (fila total: KWH y Produccion (Equivalente))",),
        ),
        ModelKey.FA_DIARIO: ModelSpec(
            key=ModelKey.FA_DIARIO,
            area="F&A",
            granularity=Granularity.DAY,
            target="kwh",
            unit="kWh",
            features=(_EQ, _SUNDAY, _NO_PRODUCTION),
            training_window=None,
            validation_periods=30,
            hac_lags=7,
            sources=(
                "data/SCADA-Base de datos F&A.xlsx (contadores de los tres rectificadores)",
                "Base de datos, Gold: EQ diarios de FORMACION y DECORADO",
            ),
        ),
    }
)


@dataclass(frozen=True, slots=True)
class Coefficient:
    """One coefficient of a trained model.

    Attributes:
        feature: the variable it multiplies, or :data:`INTERCEPT`.
        value: the coefficient, in the target's unit per unit of the variable.
        p_value: its p-value with Newey-West (HAC) errors.
        centre: the value the variable was centred on in training, or
            ``None`` when it entered as it was.
    """

    feature: str
    value: float
    p_value: float
    centre: float | None = None

    @property
    def significant(self) -> bool:
        """Whether the coefficient is distinct from zero at :data:`ALPHA`."""
        return self.p_value < ALPHA


@dataclass(frozen=True, slots=True)
class FeatureRange:
    """The values one variable took over the training window."""

    feature: str
    minimum: float
    maximum: float


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    """One period of the history a model was judged on.

    Attributes:
        period: the first day of the month, or the day.
        actual: the target's real value.
        inputs: the variables' values in that period.
        predicted: the one-step forecast of the validation, or ``None`` before
            the validation window.
        naive: the naive forecast (the previous period's value), or ``None``
            before the validation window.
    """

    period: date
    actual: float
    inputs: Mapping[str, float]
    predicted: float | None = None
    naive: float | None = None


@dataclass(frozen=True, slots=True)
class ModelMetrics:
    """How well a model fits, and how well it forecasts.

    Training figures are the mean over the validation folds of each fold's fit
    on its own training window. Validation figures come from the one-step
    forecasts of ``TimeSeriesSplit``, none of which saw its own value.

    Attributes:
        train_r2: mean R² of the folds on their training windows.
        train_wape: mean WAPE of the folds on their training windows, in %.
        validation_r2: R² of the one-step forecasts.
        validation_mae: mean absolute error of the one-step forecasts.
        validation_wape: WAPE of the one-step forecasts, in %.
        validation_mase: MAE over the naive forecast's MAE.
        naive_wape: WAPE of the naive forecast over the same periods, in %.
        diebold_mariano_p: p-value of the model against the naive forecast.
        alert_band: 1.645 standard deviations of the validation errors: the
            half-width of a 90 % band around a forecast.
        validation_periods: how many one-step forecasts there were.
    """

    train_r2: float
    train_wape: float
    validation_r2: float
    validation_mae: float
    validation_wape: float
    validation_mase: float
    naive_wape: float
    diebold_mariano_p: float
    alert_band: float
    validation_periods: int


@dataclass(frozen=True, slots=True)
class BaselineModel:
    """A trained model, as it is stored and served.

    Attributes:
        spec: what it predicts and from what.
        intercept: the intercept, with the variables at their training centre.
        coefficients: one per variable, in the order of ``spec.features``.
        metrics: its training and validation figures.
        history: every period it was trained or judged on, oldest first.
        ranges: the values each variable took over the training window.
        training_start: the first period of the training window.
        training_end: the last period of the training window.
        n_training: the periods in the training window.
        coverage: the share of the window's calendar periods with data.
        trained_at: when it was trained; ``None`` before it is stored.
    """

    spec: ModelSpec
    intercept: Coefficient
    coefficients: tuple[Coefficient, ...]
    metrics: ModelMetrics
    history: tuple[HistoryPoint, ...]
    ranges: tuple[FeatureRange, ...]
    training_start: date
    training_end: date
    n_training: int
    coverage: float
    trained_at: datetime | None = None

    def coefficient(self, feature: str) -> Coefficient:
        """Return the coefficient of one variable.

        Raises:
            KeyError: if the model has no such variable.
        """
        for coefficient in self.coefficients:
            if coefficient.feature == feature:
                return coefficient
        raise KeyError(feature)

    def range_of(self, feature: str) -> FeatureRange | None:
        """Return the training range of one variable, if it was recorded."""
        return next((item for item in self.ranges if item.feature == feature), None)

    @property
    def intercept_at_reference(self) -> float:
        """The intercept with every variable measured from its reference.

        A variable centred on its training mean in the fit is measured from
        its reference instead (``dias`` from 30), so the intercept reads in
        plant terms. The predictions do not change.
        """
        shift = 0.0
        for feature, coefficient in zip(self.spec.features, self.coefficients, strict=True):
            centre = coefficient.centre or 0.0
            shift += coefficient.value * ((feature.reference or 0.0) - centre)
        return self.intercept.value + shift

    @property
    def next_period(self) -> date:
        """The period after the last one of the history."""
        last = self.history[-1].period if self.history else self.training_end
        return next_period(last, self.spec.granularity)


@dataclass(frozen=True, slots=True)
class Term:
    """One term of a forecast's equation, with the values entered.

    Attributes:
        feature: the variable.
        coefficient: its coefficient.
        value: the value entered, or derived.
        reference: what the value is measured from, or ``None``.
        contribution: coefficient * (value - reference).
    """

    feature: str
    coefficient: float
    value: float
    reference: float | None
    contribution: float


@dataclass(frozen=True, slots=True)
class Forecast:
    """The forecast of one period for the values a person entered.

    Attributes:
        model_key: the model.
        period: the period forecast: the one after the history.
        prediction: the predicted target.
        low: the prediction minus the alert band, never below zero: energy is
            not negative.
        high: the prediction plus the alert band.
        intercept: the intercept, with the variables at their references.
        terms: one per variable, in the order of the equation.
        per_unit: the prediction per equivalent battery, when ``eq`` > 0.
        outside_training: the variables whose value lies outside the range
            the model was trained on.
    """

    model_key: ModelKey
    period: date
    prediction: float
    low: float
    high: float
    intercept: float
    terms: tuple[Term, ...]
    per_unit: float | None
    outside_training: tuple[str, ...] = field(default=())


def next_period(period: date, granularity: Granularity) -> date:
    """Return the period after ``period``: the next month's first day, or the next day."""
    if granularity is Granularity.DAY:
        return period + timedelta(days=1)
    year, month = (period.year + 1, 1) if period.month == 12 else (period.year, period.month + 1)  # noqa: PLR2004
    return date(year, month, 1)


def _derive(feature: FeatureSpec, entered: Mapping[str, float]) -> float:
    """The value of a variable a person does not enter."""
    if feature.derivation is Derivation.NO_PRODUCTION:
        return 1.0 if entered.get("eq", 0.0) <= 0 else 0.0
    raise InvalidForecastInputError("Unknown derivation", feature=feature.name)  # pragma: no cover


def _checked(spec: ModelSpec, inputs: Mapping[str, float]) -> dict[str, float]:
    """The variables entered, checked against the spec, with the derived ones added.

    Raises:
        InvalidForecastInputError: for an unknown, missing, non-finite, binary
            other than 0 or 1, or out-of-range value.
    """
    expected = {feature.name for feature in spec.features if feature.entered}
    unknown = sorted(set(inputs) - expected)
    if unknown:
        raise InvalidForecastInputError("Unknown variables", features=unknown)
    missing = sorted(expected - set(inputs))
    if missing:
        raise InvalidForecastInputError("Missing variables", features=missing)

    values: dict[str, float] = {}
    for feature in spec.features:
        if not feature.entered:
            continue
        value = float(inputs[feature.name])
        if not math.isfinite(value):
            raise InvalidForecastInputError("Not a number", feature=feature.name)
        if feature.kind is FeatureKind.BINARY and value not in {0.0, 1.0}:
            raise InvalidForecastInputError("Must be 0 or 1", feature=feature.name, value=value)
        if feature.minimum is not None and value < feature.minimum:
            raise InvalidForecastInputError(
                "Below the minimum", feature=feature.name, value=value, minimum=feature.minimum
            )
        if feature.maximum is not None and value > feature.maximum:
            raise InvalidForecastInputError(
                "Above the maximum", feature=feature.name, value=value, maximum=feature.maximum
            )
        values[feature.name] = value
    for feature in spec.features:
        if not feature.entered:
            values[feature.name] = _derive(feature, values)
    return values


def forecast(model: BaselineModel, inputs: Mapping[str, float]) -> Forecast:
    """Evaluate a model's equation for the values a person entered.

    Args:
        model: the trained model.
        inputs: one value per variable a person enters (``eq``, ``dias``,
            ``domingo``); a derived one (``sin_eq``) is worked out here.

    Returns:
        The forecast of the period after the history, its band, and every
        term of the equation with the values filled in.

    Raises:
        InvalidForecastInputError: if a value is unknown, missing or out of range.
    """
    values = _checked(model.spec, inputs)
    terms: list[Term] = []
    for feature, coefficient in zip(model.spec.features, model.coefficients, strict=True):
        value = values[feature.name]
        reference = feature.reference
        contribution = coefficient.value * (value - (reference or 0.0))
        terms.append(Term(feature.name, coefficient.value, value, reference, contribution))

    intercept = model.intercept_at_reference
    prediction = intercept + sum(term.contribution for term in terms)
    band = model.metrics.alert_band
    outside = tuple(
        name
        for name, value in values.items()
        if (span := model.range_of(name)) is not None and not span.minimum <= value <= span.maximum
    )
    production = values.get("eq", 0.0)
    return Forecast(
        model_key=model.spec.key,
        period=model.next_period,
        prediction=prediction,
        low=max(0.0, prediction - band),
        high=prediction + band,
        intercept=intercept,
        terms=tuple(terms),
        per_unit=prediction / production if production > 0 else None,
        outside_training=outside,
    )


def default_inputs(model: BaselineModel) -> dict[str, float]:
    """The values a forecast starts from: the next period, with typical production.

    ``dias`` is the length of the next month; ``domingo`` says whether the
    next day is a Sunday; ``eq`` is the median production of the last
    :data:`DEFAULT_LOOKBACK` periods that produced (and, for a day, that
    were the same kind of day).

    Returns:
        One value per variable a person enters.
    """
    period = model.next_period
    names = set(model.spec.feature_names)
    defaults: dict[str, float] = {}
    if "dias" in names:
        defaults["dias"] = float(calendar.monthrange(period.year, period.month)[1])
    is_sunday = 1.0 if period.weekday() == SUNDAY else 0.0
    if "domingo" in names:
        defaults["domingo"] = is_sunday

    def alike(point: HistoryPoint) -> bool:
        same_day = "domingo" not in names or point.inputs.get("domingo") == is_sunday
        return point.inputs.get("eq", 0.0) > 0 and same_day

    recent = [point.inputs["eq"] for point in model.history if alike(point)][-DEFAULT_LOOKBACK:]
    if "eq" in names:
        defaults["eq"] = round(statistics.median(recent)) if recent else 0.0
    return defaults


def equation(model: BaselineModel) -> str:
    """The model's equation, with every variable measured from its reference.

    For example ``kwh = 116,804 + 4.79 * eq + 6,239 * (dias - 30)``.
    """
    parts = [f"{model.spec.target} = {model.intercept_at_reference:,.0f}"]
    for feature, coefficient in zip(model.spec.features, model.coefficients, strict=True):
        sign = "-" if coefficient.value < 0 else "+"
        variable = (
            feature.name
            if feature.reference is None
            else f"({feature.name} - {feature.reference:g})"
        )
        digits = 2 if abs(coefficient.value) < 100 else 0  # noqa: PLR2004 - readable rounding
        parts.append(f"{sign} {abs(coefficient.value):,.{digits}f} * {variable}")
    return " ".join(parts)


def describe_model(model: BaselineModel) -> list[str]:
    """Lines that describe a trained model, for the command line."""
    metrics = model.metrics
    unit = "days" if model.spec.granularity is Granularity.DAY else "months"
    trained = model.trained_at.isoformat(timespec="seconds") if model.trained_at else "not stored"
    significance = ", ".join(
        f"{item.feature} p={item.p_value:.3f}" for item in (model.intercept, *model.coefficients)
    )
    return [
        f"{model.spec.key.value}: {equation(model)}",
        f"  trained on {model.training_start} to {model.training_end} "
        f"({model.n_training} {unit}, coverage {model.coverage:.0%}); stored {trained}",
        f"  training (mean of folds): R2 {metrics.train_r2:.2f}, WAPE {metrics.train_wape:.2f} %",
        f"  validation ({metrics.validation_periods} {unit} one step ahead): "
        f"R2 {metrics.validation_r2:.2f}, WAPE {metrics.validation_wape:.2f} %, "
        f"MASE {metrics.validation_mase:.2f}, naive WAPE {metrics.naive_wape:.2f} %, "
        f"Diebold-Mariano p {metrics.diebold_mariano_p:.3f}",
        f"  alert band +/- {metrics.alert_band:,.0f} {model.spec.unit}; HAC p-values: "
        f"{significance}",
    ]


def periods_between(start: date, end: date, granularity: Granularity) -> int:
    """How many calendar periods run from ``start`` to ``end``, both included."""
    if granularity is Granularity.DAY:
        return (end - start).days + 1
    return (end.year - start.year) * 12 + end.month - start.month + 1


__all__ = [
    "ALPHA",
    "INTERCEPT",
    "SPECS",
    "BaselineModel",
    "Coefficient",
    "Derivation",
    "FeatureKind",
    "FeatureRange",
    "FeatureSpec",
    "Forecast",
    "Granularity",
    "HistoryPoint",
    "ModelKey",
    "ModelMetrics",
    "ModelSpec",
    "Term",
    "default_inputs",
    "describe_model",
    "equation",
    "forecast",
    "next_period",
    "periods_between",
]
