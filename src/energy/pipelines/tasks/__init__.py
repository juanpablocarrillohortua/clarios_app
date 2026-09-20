"""Prefect tasks - the individually retryable steps a flow composes.

Layer: pipelines

Each task is idempotent and has a single responsibility, so that a failure
halfway through a flow can be retried without duplicating Bronze rows or
double-counting a KPI.
"""

from __future__ import annotations

__all__: list[str] = []
