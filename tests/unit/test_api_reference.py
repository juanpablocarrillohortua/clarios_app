"""The guide to the API names every operation the API publishes.

Layer: tests (unit)

``docs/api.md`` has a table of the endpoints: who may call each one, and whether
it is built. The table repeats what the routers define, so it would go stale
with the first endpoint added without it. These tests compare the operations
the guide names with the schema the application publishes, in both directions.

The guide writes an operation as a browser sees it, in a code span:
``GET /api/me``. Anything written that way, anywhere in the guide, must exist.
What each row says about its endpoint is not checked here: who may call a route
is the subject of ``test_api_auth.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

pytestmark = pytest.mark.unit

GUIDE = Path(__file__).resolve().parents[2] / "docs" / "api.md"
# The prefix Caddy removes before a request reaches the application.
OUTSIDE_PREFIX = "/api"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
# An operation in a code span, for example `GET /api/loads/{run_id}`.
OPERATION = re.compile(rf"`(GET|POST|PUT|PATCH|DELETE) ({re.escape(OUTSIDE_PREFIX)}/[^`\s]*)`")


def _published(app: FastAPI) -> set[str]:
    """Return every operation of the API, written as the guide writes them."""
    return {
        f"{method.upper()} {OUTSIDE_PREFIX}{path}"
        for path, item in app.openapi()["paths"].items()
        for method in item
        if method in HTTP_METHODS
    }


def _named_in_the_guide() -> set[str]:
    """Return every operation the guide names."""
    text = GUIDE.read_text(encoding="utf-8")
    return {f"{method} {path}" for method, path in OPERATION.findall(text)}


def test_the_guide_names_every_operation_of_the_api(app: FastAPI) -> None:
    """A new endpoint needs its row in the table of ``docs/api.md``."""
    missing = _published(app) - _named_in_the_guide()

    assert not missing, f"operations missing from docs/api.md: {sorted(missing)}"


def test_the_guide_names_no_operation_that_does_not_exist(app: FastAPI) -> None:
    """A removed or renamed endpoint leaves the guide with it."""
    named = _named_in_the_guide()
    assert named, "docs/api.md names no operation in the form `GET /api/...`"

    stale = named - _published(app)

    assert not stale, f"docs/api.md names operations the API does not have: {sorted(stale)}"
