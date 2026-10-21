"""``/models``: the energy baseline models through the API (ADR 0012).

The service reads an in-memory store holding the hand-made models of
``tests/fixtures/baseline_models.py``. Who may call is tested here and in
``test_api_auth.py``; the arithmetic, in ``test_baseline_models.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fixtures.baseline_models import InMemoryBaselineModelStore, daily_model, orec_model

from energy.services.baseline_model_service import BaselineModelService

# Nothing of `energy.api` is imported at the top of this module: importing it
# builds the app and freezes the settings before a test can set them.

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


@pytest.fixture
def store(app: FastAPI) -> Iterator[InMemoryBaselineModelStore]:
    """Bind the service to a store holding OREC's monthly and F&A's daily model."""
    from energy.api.deps import get_baseline_model_service  # noqa: PLC0415

    memory = InMemoryBaselineModelStore(orec_model(), daily_model())
    service = BaselineModelService(memory)
    app.dependency_overrides[get_baseline_model_service] = lambda: service
    yield memory
    app.dependency_overrides.pop(get_baseline_model_service, None)


# =============================================================================
# Reading
# =============================================================================
def test_lists_the_models_in_force(
    client: TestClient, auth_headers: Any, store: InMemoryBaselineModelStore
) -> None:
    response = client.get("/models", headers=auth_headers("viewer"))

    assert response.status_code == 200
    payload = response.json()
    assert [item["key"] for item in payload] == ["orec_mensual", "fa_diario"]
    assert payload[0] == {
        "key": "orec_mensual",
        "area": "OREC",
        "granularity": "month",
        "target": "kwh",
        "unit": "kWh",
        "trained_at": "2026-10-20T12:00:00Z",
        "training_start": "2026-04-01",
        "training_end": "2026-06-01",
        "n_training": 3,
        "coverage": 1.0,
        "validation_wape": 4.89,
        "validation_mase": 0.48,
    }


def test_one_model_in_full(
    client: TestClient, auth_headers: Any, store: InMemoryBaselineModelStore
) -> None:
    payload = client.get("/models/orec_mensual", headers=auth_headers("viewer")).json()

    assert payload["equation"] == "kwh = 100,000 + 5.00 * eq + 6,000 * (dias - 30)"
    assert payload["intercept"] == pytest.approx(100_000.0)
    assert payload["intercept_significant"] is True
    assert payload["next_period"] == "2026-07-01"
    assert payload["metrics"]["train_r2"] == 0.75
    assert payload["metrics"]["validation_r2"] == 0.91
    assert payload["metrics"]["alert_band"] == 50_000.0
    eq, days = payload["features"]
    assert eq == {
        "name": "eq",
        "kind": "numeric",
        "entered": True,
        "minimum": 0.0,
        "maximum": None,
        "reference": None,
        "training_minimum": 40_000.0,
        "training_maximum": 90_000.0,
        "default": 70_000.0,
        "coefficient": 5.0,
        "p_value": 0.00001,
        "significant": True,
    }
    assert (days["reference"], days["default"], days["significant"]) == (30.0, 31.0, False)
    assert [point["period"] for point in payload["history"]] == [
        "2026-04-01",
        "2026-05-01",
        "2026-06-01",
    ]
    assert payload["history"][0]["predicted"] is None
    assert payload["history"][1]["inputs"] == {"eq": 70_000.0, "dias": 30.0}


def test_a_derived_variable_is_listed_but_not_entered(
    client: TestClient, auth_headers: Any, store: InMemoryBaselineModelStore
) -> None:
    features = client.get("/models/fa_diario", headers=auth_headers("viewer")).json()["features"]

    assert [(item["name"], item["entered"], item["default"]) for item in features] == [
        ("eq", True, 2_050.0),
        ("domingo", True, 0.0),
        ("sin_eq", False, None),
    ]


# =============================================================================
# Forecasting
# =============================================================================
def test_a_forecast_returns_every_term(
    client: TestClient, auth_headers: Any, store: InMemoryBaselineModelStore
) -> None:
    response = client.post(
        "/models/orec_mensual/forecast",
        json={"inputs": {"eq": 70_000, "dias": 31}},
        headers=auth_headers("operator"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["key"] == "orec_mensual"
    assert payload["unit"] == "kWh"
    assert payload["period"] == "2026-07-01"
    assert payload["prediction"] == pytest.approx(456_000.0)
    assert (payload["low"], payload["high"]) == pytest.approx((406_000.0, 506_000.0))
    assert payload["terms"] == [
        {
            "feature": "eq",
            "coefficient": 5.0,
            "value": 70_000.0,
            "reference": None,
            "contribution": 350_000.0,
        },
        {
            "feature": "dias",
            "coefficient": 6_000.0,
            "value": 31.0,
            "reference": 30.0,
            "contribution": 6_000.0,
        },
    ]
    assert payload["outside_training"] == []


def test_an_extrapolation_is_answered_and_flagged(
    client: TestClient, auth_headers: Any, store: InMemoryBaselineModelStore
) -> None:
    payload = client.post(
        "/models/orec_mensual/forecast",
        json={"inputs": {"eq": 100_000, "dias": 30}},
        headers=auth_headers("viewer"),
    ).json()

    assert payload["outside_training"] == ["eq"]


# =============================================================================
# Refusals
# =============================================================================
def test_an_invalid_value_is_a_422_in_the_envelope(
    client: TestClient, auth_headers: Any, store: InMemoryBaselineModelStore
) -> None:
    response = client.post(
        "/models/orec_mensual/forecast",
        json={"inputs": {"eq": 70_000, "dias": 27}},
        headers=auth_headers("viewer"),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_forecast_input"


def test_a_field_outside_the_contract_is_refused(
    client: TestClient, auth_headers: Any, store: InMemoryBaselineModelStore
) -> None:
    response = client.post(
        "/models/orec_mensual/forecast",
        json={"inputs": {"eq": 1, "dias": 30}, "extra": 1},
        headers=auth_headers("viewer"),
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/models/ensamble_mensual", "unknown_model"),
        ("/models/fa_mensual", "model_not_trained"),
    ],
)
def test_unknown_and_untrained_models_are_404(
    client: TestClient,
    auth_headers: Any,
    store: InMemoryBaselineModelStore,
    path: str,
    code: str,
) -> None:
    response = client.get(path, headers=auth_headers("viewer"))

    assert response.status_code == 404
    assert response.json()["code"] == code


def test_a_person_without_a_role_is_refused(
    client: TestClient, auth_headers: Any, store: InMemoryBaselineModelStore
) -> None:
    response = client.get("/models", headers=auth_headers())

    assert response.status_code == 403
