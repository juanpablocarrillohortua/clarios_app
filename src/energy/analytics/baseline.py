"""Energy baseline regression.

Layer: analytics

Responsibility:
    Builds the statistical model that answers "how much energy *should* this
    plant have used, given the conditions it operated under?" Everything the
    platform reports as a saving or an overconsumption is measured against this
    baseline, which makes it the most consequential model here. It follows the
    IPMVP option C approach: regress consumption on the independent variables
    that genuinely drive it - production volume, ambient temperature, operating
    hours - fit over a documented baseline period, and hold that model fixed so
    later performance is judged against a stable reference rather than a moving
    one.

Depends on:
    ``energy.domain``, ``energy.core``, ``polars``, ``scikit-learn``,
    ``statsmodels`` (optional ``analytics`` extra).

Implemented in:
    Phase 5 - Analytics, alerting and reporting
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    import polars as pl

    from energy.domain.value_objects import TimeWindow


@dataclass(frozen=True, slots=True)
class BaselineModel:
    """A fitted energy baseline for one plant.

    Attributes:
        plant_code: the plant this model describes.
        drivers: the independent variables, in coefficient order.
        coefficients: fitted coefficients, aligned with ``drivers``.
        intercept: the fitted intercept, interpretable as base load - the energy
            drawn when every driver is zero.
        r_squared: coefficient of determination over the baseline period.
        cv_rmse: coefficient of variation of the RMSE, as a fraction. IPMVP
            considers a monthly model acceptable below 0.15; above that the
            baseline is too noisy to attribute savings with confidence.
        baseline_window: the period the model was fitted over.
        fitted_at: when it was fitted, UTC.
        observation_count: how many periods the fit used.
    """

    plant_code: str
    drivers: tuple[str, ...]
    coefficients: tuple[float, ...]
    intercept: float
    r_squared: float
    cv_rmse: float
    baseline_window: TimeWindow
    fitted_at: datetime
    observation_count: int

    @property
    def is_acceptable(self) -> bool:
        """Return whether the fit meets the IPMVP quality thresholds.

        Returns:
            ``True`` when ``r_squared`` is at least 0.75 and ``cv_rmse`` is at
            most 0.15. A model failing this is still stored - so the failure is
            visible - but must not be used to report savings.
        """
        raise NotImplementedError("Phase 5: baseline acceptability check")


def fit_baseline(
    observations: pl.DataFrame,
    plant_code: str,
    drivers: Sequence[str],
    window: TimeWindow,
) -> BaselineModel:
    """Fit a baseline regression over a documented period.

    Args:
        observations: one row per period with the energy consumed and each
            driver's value.
        plant_code: the plant being modelled.
        drivers: the independent variables to regress on.
        window: the baseline period. Must be a full operating cycle - at least
            twelve months - or seasonal effects are absorbed into the intercept
            and every later comparison is biased.

    Returns:
        The fitted :class:`BaselineModel`.

    Raises:
        ValueError: if there are fewer observations than the model needs, or a
            named driver is absent from ``observations``.
        InvalidTimeWindowError: if the baseline period is shorter than one year.
    """
    raise NotImplementedError("Phase 5: baseline regression fit")


def predict(model: BaselineModel, conditions: pl.DataFrame) -> pl.DataFrame:
    """Predict expected consumption under given operating conditions.

    Args:
        model: the fitted baseline.
        conditions: one row per period with each driver's value.

    Returns:
        The input frame with an ``expected_kwh`` column and a prediction
        interval, so a deviation can be judged against the model's own
        uncertainty rather than treated as exact.

    Raises:
        ValueError: if a driver the model needs is missing from ``conditions``.
    """
    raise NotImplementedError("Phase 5: baseline prediction")


def detect_drift(model: BaselineModel, recent: pl.DataFrame, threshold: float = 0.2) -> bool:
    """Detect whether a baseline has stopped describing the plant.

    Plants change: new equipment, new products, new shift patterns. A baseline
    fitted before such a change reports a permanent apparent saving or loss that
    reflects the change rather than performance, so drift must be detected and
    the baseline re-fitted against a new documented period.

    Args:
        model: the fitted baseline.
        recent: recent observations to test against it.
        threshold: relative degradation in CV(RMSE) that counts as drift.

    Returns:
        ``True`` when the model no longer fits recent data within the threshold.
    """
    raise NotImplementedError("Phase 5: baseline drift detection")


__all__ = ["BaselineModel", "detect_drift", "fit_baseline", "predict"]
