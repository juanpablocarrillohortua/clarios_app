"""Prefect orchestration: flows, tasks, and incremental state.

Layer: pipelines

One of three sibling entry points. Flows compose tasks; tasks call adapters
and services. This layer owns scheduling, retries and watermarks, and is
where Bronze becomes Silver becomes Gold. It must never import ``api`` or
``dashboards``.
"""

from __future__ import annotations

__all__: list[str] = []
