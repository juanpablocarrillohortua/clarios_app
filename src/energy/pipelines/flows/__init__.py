"""Prefect flows - the schedulable units of work.

Layer: pipelines

A flow wires tasks together, decides what runs in parallel, and defines the
retry and alerting policy. Flows contain no transformation logic: they call
tasks, which call services and adapters.
"""

from __future__ import annotations

__all__: list[str] = []
